from __future__ import annotations

from pathlib import Path
import random
import tempfile
import uuid

import modal

try:
    from modal_runtime import ENV_RELATIVE_PATH, app, run_rlm_remote, shared_volume
except ImportError:
    from rlm.modal_runtime import ENV_RELATIVE_PATH, app, run_rlm_remote, shared_volume


def generate_massive_context_file(context_path: Path, num_lines: int = 1_000_000, answer: str = "1298418") -> int:
    print("Generating massive context with 1M lines...")

    # Set of random words to use
    random_words = ["blah", "random", "text", "data", "content", "information", "sample"]

    # Insert the magic number at a random position (somewhere in the middle)
    magic_position = random.randint(400000, 600000)

    with open(context_path, "w", encoding="utf-8") as file:
        for i in range(num_lines):
            if i == magic_position:
                line = f"The magic number is {answer}"
            else:
                num_words = random.randint(3, 8)
                line_words = [random.choice(random_words) for _ in range(num_words)]
                line = " ".join(line_words)
            file.write(line)
            if i < num_lines - 1:
                file.write("\n")

    print(f"Magic number inserted at position {magic_position}")
    return magic_position


def resolve_env_file() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    candidates = [
        project_root / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not find .env file. Expected one at project root (.env) or rlm/.env."
    )


def upload_inputs_to_volume(context_file: Path, env_file: Path) -> str:
    run_id = uuid.uuid4().hex
    context_relpath = f"runs/{run_id}/context.txt"
    with shared_volume.batch_upload(force=True) as batch:
        batch.put_file(str(context_file), context_relpath)
        batch.put_file(str(env_file), ENV_RELATIVE_PATH)
    return context_relpath

def main():
    print("Example of using RLM (REPL) on Modal with a needle-in-haystack problem.")
    answer = str(random.randint(1000000, 9999999))
    env_file = resolve_env_file()
    query = "I'm looking for a magic number. What is it?"

    with tempfile.TemporaryDirectory(prefix="rlm_context_") as tmp_dir:
        context_file = Path(tmp_dir) / "context.txt"
        generate_massive_context_file(context_file, num_lines=1_000_000, answer=answer)
        context_relpath = upload_inputs_to_volume(context_file, env_file)
    with modal.enable_output():
        with app.run():
            result = run_rlm_remote.remote(
                query=query,
                context_relpath=context_relpath,
                model="gpt-5",
                recursive_model="gpt-5-nano",
                max_iterations=10,
            )
    print(f"Result: {result}. Expected: {answer}")

if __name__ == "__main__":
    main()
