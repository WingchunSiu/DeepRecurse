from __future__ import annotations

import json
from pathlib import Path
import random
import time
import tempfile
from dotenv import load_dotenv
from openai import OpenAI

TOTAL_LINES = 500
CHUNK_LINES = 500
BATCH_SEED_LINES = 100
GEN_MODEL = "gpt-5-mini"


def build_injected_fact_entries() -> list[dict[str, str | list[str]]]:
    return [
        {
            "fact": "Duplicate writes root cause: todo-service/src/sync/apply_remote_patch.py::merge_remote_changes was appending remote tasks without checking mutation_id.",
            "lines": [
                "========================================",
                "timestamp: 2026-01-10T09:12:03Z",
                "author: arya",
                "branch: hotfix/dedupe-writes",
                "topic: duplicate todo writes in sync layer",
                "message: Found root cause in todo-service/src/sync/apply_remote_patch.py function merge_remote_changes.",
                "message: We append remote tasks but never guard by mutation_id, so retries duplicate rows.",
                "message: Also saw duplicate path when offline queue replays old payloads.",
            ],
        },
        {
            "fact": "Fix agreed: add idempotency gate on mutation_id and upsert by task_id before append.",
            "lines": [
                "========================================",
                "timestamp: 2026-01-10T10:48:27Z",
                "author: mina",
                "branch: hotfix/dedupe-writes",
                "topic: final fix decision",
                "message: Agreed fix is idempotency gate on mutation_id inside merge_remote_changes.",
                "message: Then upsert by task_id before append so retries become no-op updates.",
                "message: Keep behavior deterministic across device reconnects.",
            ],
        },
        {
            "fact": "Test file updated: todo-service/tests/test_sync_dedupe.py with cases for replayed mutations.",
            "lines": [
                "========================================",
                "timestamp: 2026-01-10T11:35:02Z",
                "author: ben",
                "branch: test/sync-regression",
                "topic: regression tests",
                "message: Added todo-service/tests/test_sync_dedupe.py.",
                "message: Cases include replayed mutation_id and repeated task payloads.",
                "message: Verifies no duplicate writes and stable ordering.",
            ],
        },
        {
            "fact": "Server-side note: do not patch ui/store/reducer.ts for this bug; issue is server sync merge path.",
            "lines": [
                "========================================",
                "timestamp: 2026-01-10T12:04:41Z",
                "author: cathy",
                "branch: docs/incident-notes",
                "topic: scope clarification",
                "message: Confirmed this is not a ui/store/reducer.ts issue.",
                "message: Root bug is server sync merge path in apply_remote_patch.py.",
                "message: UI only surfaced duplicate rows written upstream.",
            ],
        },
        {
            "fact": "Postmortem summary repeats same final remedy in merge_remote_changes.",
            "lines": [
                "========================================",
                "timestamp: 2026-01-11T08:17:55Z",
                "author: dylan",
                "branch: postmortem/sync-incident",
                "topic: incident summary",
                "message: Final remedy: in merge_remote_changes add mutation_id dedupe gate + task_id upsert.",
                "message: This stops duplicate inserts when sync retries or queue replays.",
                "message: Ship behind sync_dedupe_guard feature flag for one day.",
            ],
        },
    ]


def build_chunk_messages(chunk_index: int, line_count: int, style_seed: int) -> list[dict[str, str]]:
    system_msg = (
        "You generate raw plaintext engineering chat logs. "
        "Do not use markdown or code fences. Keep lines short and realistic."
    )
    user_msg = (
        f"Generate exactly {line_count} lines of fictional developer chat logs for a TODO app project.\n"
        "Output rules:\n"
        "- Exactly one line per newline.\n"
        "- Include repeated sections delimited by: ========================================\n"
        "- After delimiters include metadata lines like timestamp/author/branch/topic.\n"
        "- Then include short message lines discussing bugs, features, tests, CI, refactors, and noise.\n"
        "- Keep logs plausible but messy and unstructured.\n"
        "- Do NOT include these exact filenames: todo-service/src/sync/apply_remote_patch.py, todo-service/tests/test_sync_dedupe.py\n"
        f"- Chunk index: {chunk_index}\n"
        f"- Variation seed: {style_seed}\n"
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def build_batch_requests(total_lines: int, chunk_lines: int) -> tuple[list[dict], dict[int, int]]:
    num_chunks = (total_lines + chunk_lines - 1) // chunk_lines
    requests: list[dict] = []
    expected_sizes: dict[int, int] = {}
    for idx in range(num_chunks):
        line_count = min(chunk_lines, total_lines - (idx * chunk_lines))
        expected_sizes[idx] = line_count
        requests.append(
            {
                "custom_id": f"chunk-{idx:03d}",
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": GEN_MODEL,
                    "input": build_chunk_messages(
                        chunk_index=idx,
                        line_count=line_count,
                        style_seed=random.randint(0, 1_000_000),
                    ),
                    "max_output_tokens": 2000,
                    "reasoning": {"effort": "low"},
                },
            }
        )
    return requests, expected_sizes


def parse_chunk_index(custom_id: str) -> int:
    return int(custom_id.split("-", 1)[1])


def coerce_to_exact_lines(lines: list[str], line_count: int, chunk_index: int) -> list[str]:
    clean = [line.rstrip() for line in lines if line.strip()]
    if len(clean) < line_count:
        deficit = line_count - len(clean)
        for i in range(deficit):
            clean.append(
                f"message: filler {chunk_index}-{i} about todo sync, CI retries, and backlog grooming"
            )
    return clean[:line_count]


def generate_base_lines_with_batch(client: OpenAI, total_lines: int) -> list[str]:
    requests, expected_sizes = build_batch_requests(total_lines=total_lines, chunk_lines=CHUNK_LINES)
    print(f"Prepared {len(requests)} batch requests for {total_lines} lines total.")

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as temp_file:
        input_jsonl_path = Path(temp_file.name)
        for req in requests:
            temp_file.write(json.dumps(req) + "\n")
    print(f"Wrote batch input JSONL to {input_jsonl_path}")

    print("Uploading batch input file...")
    with open(input_jsonl_path, "rb") as handle:
        input_file = client.files.create(file=handle, purpose="batch")
    print(f"Uploaded input file: {input_file.id}")

    print("Creating batch job...")
    batch = client.batches.create(
        input_file_id=input_file.id,
        endpoint="/v1/responses",
        completion_window="24h",
    )
    print(f"Batch created: {batch.id} (status={batch.status})")

    start = time.perf_counter()
    terminal_states = {"completed", "failed", "cancelled", "expired"}
    while batch.status not in terminal_states:
        elapsed = time.perf_counter() - start
        counts = getattr(batch, "request_counts", None)
        if counts is not None:
            print(
                f"[{elapsed:6.1f}s] status={batch.status} "
                f"completed={counts.completed} failed={counts.failed} total={counts.total}"
            )
        else:
            print(f"[{elapsed:6.1f}s] status={batch.status}")
        time.sleep(5)
        batch = client.batches.retrieve(batch.id)

    total_elapsed = time.perf_counter() - start
    print(f"Batch terminal status: {batch.status} after {total_elapsed:.1f}s")
    if batch.status != "completed":
        raise RuntimeError(f"Batch did not complete successfully: status={batch.status}")
    if not batch.output_file_id:
        raise RuntimeError("Batch completed without output_file_id.")

    print(f"Downloading batch output file: {batch.output_file_id}")
    output_text = client.files.content(batch.output_file_id).text
    output_lines = [line for line in output_text.splitlines() if line.strip()]
    print(f"Downloaded {len(output_lines)} output records.")

    chunk_results: dict[int, list[str]] = {}
    for raw_line in output_lines:
        record = json.loads(raw_line)
        custom_id = record.get("custom_id")
        if not custom_id:
            continue
        chunk_idx = parse_chunk_index(custom_id)

        response = record.get("response")
        if not response or response.get("status_code") != 200:
            print(f"Chunk {chunk_idx} returned non-200 or missing response; filling with fallback lines.")
            chunk_results[chunk_idx] = []
            continue

        body = response.get("body", {})
        content = body.get("output_text", "") or ""
        if not content:
            output_items = body.get("output", [])
            text_parts: list[str] = []
            for item in output_items:
                if item.get("type") != "message":
                    continue
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        text_parts.append(part.get("text", ""))
            content = "\n".join(part for part in text_parts if part)
        chunk_results[chunk_idx] = content.splitlines()
        print(f"Parsed chunk {chunk_idx}: raw model lines={len(chunk_results[chunk_idx])}")

    assembled: list[str] = []
    for idx in sorted(expected_sizes):
        expected_count = expected_sizes[idx]
        raw_lines = chunk_results.get(idx, [])
        normalized = coerce_to_exact_lines(raw_lines, expected_count, idx)
        print(f"Chunk {idx}: normalized to {len(normalized)} lines")
        assembled.extend(normalized)

    assembled = assembled[:total_lines]
    print(f"Assembled {len(assembled)} base lines from batch output.")
    return assembled


def expand_lines(seed_lines: list[str], target_lines: int) -> list[str]:
    if not seed_lines:
        seed_lines = ["message: fallback generated line about todo app backlog and sync status"]

    expanded: list[str] = []
    idx = 0
    while len(expanded) < target_lines:
        source = seed_lines[idx % len(seed_lines)]
        if source.startswith("timestamp:"):
            source = f"timestamp: 2026-01-{(idx % 28) + 1:02d}T{(idx % 24):02d}:{(idx % 60):02d}:00Z"
        elif source.startswith("author:"):
            source = f"author: dev_{idx % 17}"
        elif source.startswith("branch:"):
            source = f"branch: feature/todo-{idx % 31}"
        elif source.startswith("message:"):
            source = f"{source} [thread-{idx % 23}]"
        expanded.append(source)
        idx += 1
    return expanded


def inject_facts_into_lines(
    base_lines: list[str],
    fact_entries: list[dict[str, str | list[str]]],
) -> list[str]:
    if not fact_entries:
        return base_lines

    insertion_positions = sorted(
        random.sample(range(100, len(base_lines) - 100), k=len(fact_entries))
    )
    insertion_map: dict[int, list[str]] = {
        pos: entry["lines"] for pos, entry in zip(insertion_positions, fact_entries)
    }

    result: list[str] = []
    for idx, line in enumerate(base_lines):
        if idx in insertion_map:
            result.extend(insertion_map[idx])
        result.append(line)
    return result


def generate_context_file(context_path: Path) -> tuple[str, str, list[str]]:
    print("Generating monocontext-style chat logs with gpt-5-mini via Batch API...")
    load_dotenv()
    client = OpenAI()

    injected_entries = build_injected_fact_entries()
    injected_line_count = sum(len(entry["lines"]) for entry in injected_entries)
    base_target = TOTAL_LINES - injected_line_count
    if base_target <= 0:
        raise ValueError("Injected fact lines exceed TOTAL_LINES.")

    total_start = time.perf_counter()
    seed_target = min(base_target, BATCH_SEED_LINES)
    print(
        f"Generating {seed_target} seed lines with Batch API, then expanding locally to {base_target} lines."
    )
    seed_lines = generate_base_lines_with_batch(client=client, total_lines=seed_target)
    all_lines = expand_lines(seed_lines=seed_lines, target_lines=base_target)
    print(f"Expanded to {len(all_lines)} base lines.")

    mixed_lines = inject_facts_into_lines(all_lines, injected_entries)
    mixed_lines = mixed_lines[:TOTAL_LINES]

    with open(context_path, "w", encoding="utf-8") as file:
        file.write("\n".join(mixed_lines))
    total_elapsed = time.perf_counter() - total_start
    print(
        f"Finished generation in {total_elapsed:.1f}s. "
        f"Wrote {len(mixed_lines)} lines to {context_path}"
    )

    query = (
        "Looking through the monocontext todo app logs, which file and function were identified "
        "as causing duplicate todo writes, and what exact fix was agreed?"
    )
    expected_answer = (
        "Root cause was todo-service/src/sync/apply_remote_patch.py in merge_remote_changes; "
        "fix was adding a mutation_id idempotency gate and upserting by task_id before append."
    )
    injected_facts = [entry["fact"] for entry in injected_entries]
    return query, expected_answer, injected_facts


def main() -> None:
    output_path = Path(__file__).resolve().parent / "mctx_generated_test.txt"
    query, expected_answer, injected_facts = generate_context_file(output_path)

    print("\n--- QUERY ---")
    print(query)
    print("\n--- EXPECTED ---")
    print(expected_answer)
    print("\n--- INJECTED FACTS ---")
    for idx, fact in enumerate(injected_facts, start=1):
        print(f"{idx}. {fact}")
    print(f"\nGenerated file: {output_path}")


if __name__ == "__main__":
    main()
