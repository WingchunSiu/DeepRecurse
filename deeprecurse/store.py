"""Upload transcript files to the Modal Volume.

Usage:
    # Upload a raw transcript file (alternating USER:/ASSISTANT: lines)
    python -m deeprecurse.store transcript.txt --repo myrepo --session abc123

    # Pipe from stdin
    cat transcript.txt | python -m deeprecurse.store - --repo myrepo --session abc123

Volume layout produced:
    /transcripts/{repo}/{session_id}/turn-001.json
    /transcripts/{repo}/{session_id}/turn-002.json
    ...
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid


def parse_transcript(text: str) -> list[dict]:
    """Parse a raw transcript into a list of turn dicts.

    Expects lines starting with ``USER:`` or ``ASSISTANT:`` as turn
    boundaries.  Everything between two boundaries is treated as a single
    turn's content (supports multi-line content).
    """
    turns: list[dict] = []
    current_role: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        match = re.match(r"^(USER|ASSISTANT):\s*(.*)", line)
        if match:
            if current_role is not None:
                turns.append({
                    "role": current_role,
                    "content": "\n".join(current_lines).strip(),
                })
            current_role = match.group(1).lower()
            current_lines = [match.group(2)]
        else:
            current_lines.append(line)

    if current_role is not None:
        turns.append({
            "role": current_role,
            "content": "\n".join(current_lines).strip(),
        })

    return turns


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a transcript file to the Modal Volume as structured turn JSON."
    )
    parser.add_argument(
        "file",
        help="Path to transcript file, or '-' for stdin.",
    )
    parser.add_argument("--repo", required=True, help="Repository name.")
    parser.add_argument(
        "--session",
        default=None,
        help="Session ID (auto-generated if omitted).",
    )
    args = parser.parse_args()

    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(args.file) as f:
            text = f.read()

    session_id = args.session or uuid.uuid4().hex[:12]
    turns = parse_transcript(text)

    if not turns:
        print("No turns found in input.", file=sys.stderr)
        sys.exit(1)

    from deeprecurse.modal_app import app, store_transcript

    with app.run():
        paths = store_transcript.remote(turns=turns, repo=args.repo, session_id=session_id)
    print(f"Stored {len(paths)} turns:")
    for p in paths:
        print(f"  {p}")


if __name__ == "__main__":
    main()
