"""Modal runtime wiring for RLM root function and shared resources."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import modal
from dotenv import load_dotenv

MODAL_APP_NAME = "rlm-repl"
MODAL_VOLUME_NAME = "rlm-shared-volume"
MOUNT_PATH = "/rlm-data"
SOURCE_PATH_IN_IMAGE = "/root/rlm-app"
ENV_RELATIVE_PATH = ".env"
LOCAL_SOURCE_DIR = Path(__file__).resolve().parent

app = modal.App(MODAL_APP_NAME)
print("LOCAL_SOURCE_DIR: ", LOCAL_SOURCE_DIR)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("openai", "python-dotenv", "rich")
    .add_local_dir(str(LOCAL_SOURCE_DIR), remote_path=SOURCE_PATH_IN_IMAGE, copy=True)
    .run_commands(
        "echo '[image-debug] SOURCE_PATH_IN_IMAGE=/root/rlm-app'",
        "echo '[image-debug] ls -la /root/rlm-app' && ls -la /root/rlm-app || true",
        "echo '[image-debug] ls -la /root/rlm-app/rlm' && ls -la /root/rlm-app/rlm || true",
        "echo '[image-debug] find /root/rlm-app -maxdepth 2 -type d' && find /root/rlm-app -maxdepth 2 -type d | sort || true",
    )
)

shared_volume = modal.Volume.from_name(MODAL_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=image,
    volumes={MOUNT_PATH: shared_volume},
    timeout=3600,
)
def run_rlm_remote(
    query: str,
    context_relpath: str,
    model: str = "gpt-5",
    recursive_model: str = "gpt-5-nano",
    max_iterations: int = 10,
) -> str:
    """Run RLM_REPL on Modal with context read from a mounted volume file."""

    sys.path.insert(0, SOURCE_PATH_IN_IMAGE)

    from rlm.rlm_repl import RLM_REPL

    env_path = os.path.join(MOUNT_PATH, ENV_RELATIVE_PATH)
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)

    normalized_relpath = context_relpath.lstrip("/")
    context_path = os.path.join(MOUNT_PATH, normalized_relpath)
    if not os.path.exists(context_path):
        raise FileNotFoundError(f"Context file not found: {context_path}")

    sandbox_image_id = getattr(image, "object_id", None)
    print(f"[root-debug] sandbox_image_id={sandbox_image_id}")

    rlm = RLM_REPL(
        model=model,
        recursive_model=recursive_model,
        max_iterations=max_iterations,
        enable_logging=True,
        sub_rlm_mode="modal_sandbox",
        sandbox_app=app,
        sandbox_image=None,
        sandbox_image_id=sandbox_image_id,
        sandbox_volumes={MOUNT_PATH: shared_volume},
        sandbox_workdir=SOURCE_PATH_IN_IMAGE,
        env_file_path=env_path,
        sub_rlm_timeout=600,
    )
    return rlm.completion(query=query, context_path=context_path)
