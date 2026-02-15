"""Modal sandbox worker for sub-RLM calls.

Protocol:
- Reads a single JSON payload from stdin.
- Emits model response text to stdout.
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

def _debug(message: str) -> None:
    sys.stderr.write(f"[sub_rlm_worker] {message}\n")
    sys.stderr.flush()


def _read_payload() -> dict:
    raw = sys.stdin.buffer.read()
    if not raw:
        raise ValueError("No payload received on stdin")
    return json.loads(raw.decode("utf-8"))


def main() -> int:
    try:
        _debug(f"cwd={os.getcwd()}")
        _debug(f"python_executable={sys.executable}")
        _debug(f"pythonpath={os.environ.get('PYTHONPATH')}")
        _debug(f"sys.path={sys.path}")
        _debug(f"has_/root/rlm-app={os.path.exists('/root/rlm-app')}")
        _debug(f"has_/root/rlm-app/rlm={os.path.exists('/root/rlm-app/rlm')}")

        payload = _read_payload()
        env_file_path = payload.get("env_file_path")
        if env_file_path:
            load_dotenv(env_file_path, override=True)

        prompt = payload.get("prompt")
        model = payload.get("model", "gpt-5")

        from rlm.utils.llm import OpenAIClient

        client = OpenAIClient(model=model)
        response = client.completion(messages=prompt, timeout=300)
        sys.stdout.write(response or "")
        return 0
    except Exception as exc:
        sys.stderr.write(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
