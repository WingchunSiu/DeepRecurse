"""Modal App that runs TranscriptRLM with a persistent Modal Volume.

Volume layout:
    /transcripts/{repo}/{session_id}/turn-001.json
                                    /turn-002.json

Setup:
    modal volume create deeprecurse-transcripts
    modal secret create openai-secret OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

from pathlib import Path

import modal

from deeprecurse.config import (
    MAX_ITERATIONS,
    MODAL_APP_NAME,
    MODAL_IMAGE_PYTHON,
    MODAL_SECRET_NAME,
    MOUNT_PATH,
    RECURSIVE_MODEL,
    ROOT_MODEL,
    VOLUME_NAME,
)

app = modal.App(MODAL_APP_NAME)

_here = Path(__file__).resolve().parent  # deeprecurse/
_project_root = _here.parent  # DeepRecurse/

image = (
    modal.Image.debian_slim(python_version=MODAL_IMAGE_PYTHON)
    .uv_pip_install("openai", "python-dotenv", "rich")
    .env({"PYTHONPATH": "/root"})
    .add_local_dir(
        _project_root / "rlm" / "rlm",
        remote_path="/root/rlm",
    )
    .add_local_dir(
        _project_root / "deeprecurse",
        remote_path="/root/deeprecurse",
    )
)

volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
    volumes={MOUNT_PATH: volume},
    timeout=600,
)
def run_query(query: str, repo: str) -> str:
    """Run a transcript-aware RLM query inside Modal.

    Args:
        query: The natural-language question to answer.
        repo: Repository name — subdirectory inside the volume.

    Returns:
        The final answer string from RLM.
    """
    from deeprecurse.rlm_runner import TranscriptRLM

    transcript_dir = f"{MOUNT_PATH}/{repo}"

    rlm = TranscriptRLM(
        transcript_dir=transcript_dir,
        model=ROOT_MODEL,
        recursive_model=RECURSIVE_MODEL,
        max_iterations=MAX_ITERATIONS,
        enable_logging=True,
    )

    sessions = rlm._list_sessions()
    context = (
        f"Transcript store at {transcript_dir}\n"
        f"Available sessions: {sessions}\n"
        f"Use the transcript helper functions to explore the data."
    )

    return rlm.completion(context=context, query=query)


@app.function(
    image=image,
    volumes={MOUNT_PATH: volume},
    timeout=60,
)
def store_transcript(turns: list[dict], repo: str, session_id: str) -> list[str]:
    """Write parsed transcript turns to the Modal Volume.

    Args:
        turns: List of dicts with "role" and "content" keys.
        repo: Repository name.
        session_id: Session identifier.

    Returns:
        List of file paths written.
    """
    import json
    import os
    from datetime import datetime, timezone

    session_dir = f"{MOUNT_PATH}/{repo}/{session_id}"
    os.makedirs(session_dir, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    paths: list[str] = []

    for i, turn in enumerate(turns, start=1):
        obj = {
            "turn_number": i,
            "role": turn["role"],
            "content": turn["content"],
            "timestamp": now,
            "session_id": session_id,
        }
        path = f"{session_dir}/turn-{i:03d}.json"
        with open(path, "w") as f:
            json.dump(obj, f, indent=2)
        paths.append(path)

    volume.commit()
    return paths


@app.function(
    image=image,
    volumes={MOUNT_PATH: volume},
    timeout=60,
)
def list_transcripts(repo: str) -> dict:
    """List sessions and turns from the volume."""
    import os

    transcript_dir = f"{MOUNT_PATH}/{repo}"
    result: dict = {}

    if not os.path.isdir(transcript_dir):
        return {"error": f"No transcripts found for repo '{repo}'"}

    for entry in sorted(os.listdir(transcript_dir)):
        session_path = os.path.join(transcript_dir, entry)
        if os.path.isdir(session_path):
            turns = sorted(
                f for f in os.listdir(session_path) if f.endswith(".json")
            )
            result[entry] = turns

    return result


@app.local_entrypoint()
def main():
    """Quick smoke test — list transcripts from a test repo."""
    result = list_transcripts.remote(repo="test")
    print("Transcripts found:", result)
