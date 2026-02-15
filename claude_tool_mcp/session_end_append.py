"""Append a Claude SessionEnd transcript JSONL to the shared Modal context file."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RLM_DIR = PROJECT_ROOT / "rlm"
if str(RLM_DIR) not in sys.path:
    sys.path.insert(0, str(RLM_DIR))

from modal_runtime import app, append_context_remote


DEFAULT_CONTEXT_RELPATH = "runs/0fad4ca550eb4d818b81a00c0f897218/context.txt"


def _payload_from_stdin() -> dict:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from stdin: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Hook payload must be a JSON object")
    return payload


def _format_block(
    transcript_path: Path,
    transcript_text: str,
    session_id: str | None,
    reason: str | None,
) -> str:
    sid = (session_id or transcript_path.stem or "unknown-session").strip()
    why = (reason or "unknown").strip()
    uploaded_at = datetime.now(timezone.utc).isoformat()
    return (
        "\n\n"
        + "=" * 72
        + "\n"
        + f"SESSION END UPLOAD: {sid}\n"
        + f"reason: {why}\n"
        + f"uploaded_at: {uploaded_at}\n"
        + f"transcript_path: {transcript_path}\n"
        + "=" * 72
        + "\n"
        + transcript_text.rstrip()
        + "\n"
    )


def append_session_jsonl(
    transcript_path: Path,
    session_id: str | None,
    reason: str | None,
    context_relpath: str,
) -> None:
    if not transcript_path.is_file():
        raise FileNotFoundError(f"Transcript JSONL not found: {transcript_path}")

    transcript_text = transcript_path.read_text(encoding="utf-8").strip()
    if not transcript_text:
        raise ValueError(f"Transcript file is empty: {transcript_path}")

    block = _format_block(
        transcript_path=transcript_path,
        transcript_text=transcript_text,
        session_id=session_id,
        reason=reason,
    )

    with app.run():
        append_context_remote.remote(context_relpath=context_relpath, content=block)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append SessionEnd transcript to shared context")
    parser.add_argument("--stdin-json", action="store_true", help="Read hook payload from stdin")
    parser.add_argument("--transcript-path", help="Path to Claude session transcript JSONL")
    parser.add_argument("--session-id", default=None, help="Session identifier")
    parser.add_argument("--reason", default=None, help="Session end reason")
    parser.add_argument(
        "--context-relpath",
        default=DEFAULT_CONTEXT_RELPATH,
        help="Relative path under Modal volume mount",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    transcript_path: str | None = args.transcript_path
    session_id: str | None = args.session_id
    reason: str | None = args.reason

    if args.stdin_json:
        payload = _payload_from_stdin()
        transcript_path = payload.get("transcript_path") or transcript_path
        session_id = payload.get("session_id") or session_id
        reason = payload.get("reason") or reason

    if not transcript_path:
        raise ValueError("Missing transcript path. Provide --transcript-path or --stdin-json payload.")

    append_session_jsonl(
        transcript_path=Path(transcript_path).expanduser(),
        session_id=session_id,
        reason=reason,
        context_relpath=args.context_relpath,
    )
    print(
        f"Appended session {session_id or Path(transcript_path).stem} "
        f"from {transcript_path} to {args.context_relpath}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
