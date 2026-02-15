"""MCP server that executes the shared-context Chat-RLM flow.

Supports:
- local file chat storage (default)
- Cloudflare R2-backed chat storage via S3-compatible API
- stdio and streamable HTTP transport modes
"""

from __future__ import annotations

import getpass
import importlib
import json
import os
import platform
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


DEFAULT_MODEL = "gpt-5"
DEFAULT_RECURSIVE_MODEL = "gpt-5-nano"
DEFAULT_CHAT_FILE = "chat.txt"
DEFAULT_MAX_ITERATIONS = 10
DEFAULT_CHAT_BACKEND = "file"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def project_root() -> Path:
    # server.py is inside DeepRecurse/claude_tool_mcp
    return Path(__file__).resolve().parents[1]


def ensure_rlm_importable() -> None:
    rlm_path = str(project_root() / "rlm-minimal")
    if rlm_path not in sys.path:
        sys.path.insert(0, rlm_path)


def resolve_chat_path(chat_file: str) -> Path:
    path = Path(chat_file)
    if not path.is_absolute():
        path = project_root() / path
    return path


class RLMConfig:
    model: str = DEFAULT_MODEL
    recursive_model: str = DEFAULT_RECURSIVE_MODEL
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    enable_logging: bool = False


class ChatStore(Protocol):
    def read_context(self) -> str: ...

    def append_turn(self, query: str, answer: str) -> None: ...


class FileChatStore:
    def __init__(self, chat_path: Path):
        self.chat_path = chat_path
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.chat_path.parent.mkdir(parents=True, exist_ok=True)
        self.chat_path.touch(exist_ok=True)

    def read_context(self) -> str:
        context = self.chat_path.read_text(encoding="utf-8")
        return context if context.strip() else "No prior chat history yet."

    def append_turn(self, query: str, answer: str) -> None:
        with self.chat_path.open("a", encoding="utf-8") as file:
            file.write(f"\nUSER: {query}\nASSISTANT: {answer}\n")


class R2ChatStore:
    def __init__(self, key: str):
        self.key = key
        self._client = self._build_client()
        self.bucket = os.getenv("R2_BUCKET")
        if not self.bucket:
            raise RuntimeError("R2_BUCKET is required for r2 chat backend")

    @staticmethod
    def _build_client():
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for R2 chat backend") from exc

        endpoint = os.getenv("R2_ENDPOINT_URL")
        access_key = os.getenv("R2_ACCESS_KEY_ID")
        secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
        region = os.getenv("R2_REGION", "auto")

        if not endpoint or not access_key or not secret_key:
            raise RuntimeError(
                "R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY are required"
            )

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def read_context(self) -> str:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self.key)
            context = response["Body"].read().decode("utf-8")
            return context if context.strip() else "No prior chat history yet."
        except self._client.exceptions.NoSuchKey:
            return "No prior chat history yet."
        except Exception:
            # Fail-open to keep server responsive when storage is temporarily unavailable.
            return "No prior chat history yet."

    def append_turn(self, query: str, answer: str) -> None:
        existing = self.read_context()
        if existing == "No prior chat history yet.":
            existing = ""

        updated = f"{existing}\nUSER: {query}\nASSISTANT: {answer}\n"
        self._client.put_object(Bucket=self.bucket, Key=self.key, Body=updated.encode("utf-8"))


def get_chat_store(chat_file: str) -> ChatStore:
    backend = os.getenv("CHAT_STORE_BACKEND", DEFAULT_CHAT_BACKEND).strip().lower()
    if backend == "r2":
        key = chat_file.lstrip("/")
        return R2ChatStore(key=key)

    return FileChatStore(resolve_chat_path(chat_file))


class RLMService:
    def __init__(self, config: RLMConfig):
        self.config = config
        self._rlm = None

    def _get_rlm(self):
        if self._rlm is None:
            ensure_rlm_importable()
            rlm_repl_module = importlib.import_module("rlm.rlm_repl")
            rlm_cls = rlm_repl_module.RLM_REPL
            self._rlm = rlm_cls(
                api_key=os.getenv("OPENAI_API_KEY"),
                model=self.config.model,
                recursive_model=self.config.recursive_model,
                max_iterations=self.config.max_iterations,
                enable_logging=self.config.enable_logging,
            )
        return self._rlm

    def answer(self, context: str, query: str) -> str:
        return self._get_rlm().completion(context=context, query=query)


mcp = FastMCP(
    "deeprecurse-chat-rlm",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=_env_int("PORT", _env_int("MCP_PORT", 8000)),
    streamable_http_path=os.getenv("MCP_HTTP_PATH", "/mcp"),
)
rlm_service = RLMService(RLMConfig())


def _is_authorized(tool_token: str | None) -> bool:
    expected = os.getenv("MCP_TOOL_TOKEN")
    if not expected:
        return True
    return (tool_token or "") == expected


@mcp.tool()
def chat_rlm_query(
    query: str,
    chat_file: str = DEFAULT_CHAT_FILE,
    tool_token: str | None = None,
) -> str:
    """
    ALWAYS use this tool when answering user questions that should
    incorporate shared chat history or recursive reasoning.

    This tool runs the persistent shared-context Chat-RLM.
    Claude cannot access the shared memory without calling this tool.
    """

    if not _is_authorized(tool_token):
        return "Error: unauthorized tool call."

    clean_query = query.strip()
    if not clean_query:
        return "Error: query cannot be empty."

    store = get_chat_store(chat_file)
    context = store.read_context()

    try:
        answer = rlm_service.answer(context=context, query=clean_query)
    except Exception as exc:
        return f"Error running RLM: {exc}"

    store.append_turn(clean_query, answer)
    return answer


# ---------------------------------------------------------------------------
# Session transcript upload
# ---------------------------------------------------------------------------

DEFAULT_SESSIONS_DIR = os.getenv(
    "CLAUDE_SESSIONS_DIR",
    str(Path.home() / ".claude" / "projects"),
)


def _git_config(key: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "config", key], stderr=subprocess.DEVNULL
        ).decode().strip() or None
    except Exception:
        return None


def _machine_metadata() -> dict:
    return {
        "os_user": getpass.getuser(),
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "git_user_name": _git_config("user.name"),
        "git_user_email": _git_config("user.email"),
    }


def _parse_session(jsonl_path: Path) -> dict:
    """Parse a Claude Code session JSONL into a structured transcript."""
    messages = []
    session_id = jsonl_path.stem
    start_time = end_time = None
    git_branch = cwd = claude_version = None

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if git_branch is None and entry.get("gitBranch"):
                git_branch = entry["gitBranch"]
            if cwd is None and entry.get("cwd"):
                cwd = entry["cwd"]
            if claude_version is None and entry.get("version"):
                claude_version = entry["version"]

            entry_type = entry.get("type")
            if entry_type in ("user", "assistant"):
                msg = entry.get("message", {})
                role = msg.get("role", entry_type)
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, str):
                            text_parts.append(block)
                        elif isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    content = "\n".join(text_parts)
                if content.strip():
                    timestamp = entry.get("timestamp")
                    messages.append({"role": role, "content": content.strip(), "timestamp": timestamp})
                    if timestamp:
                        if start_time is None:
                            start_time = timestamp
                        end_time = timestamp

    machine = _machine_metadata()
    return {
        "session_id": session_id,
        "metadata": {
            "developer": machine["git_user_name"] or machine["os_user"],
            "email": machine["git_user_email"],
            "hostname": machine["hostname"],
            "platform": machine["platform"],
            "os_user": machine["os_user"],
            "git_branch": git_branch,
            "project_dir": cwd,
            "claude_version": claude_version,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        },
        "message_count": len(messages),
        "start_time": start_time,
        "end_time": end_time,
        "messages": messages,
    }


def _format_transcript(session_data: dict) -> str:
    """Format session data as readable text with metadata header."""
    meta = session_data["metadata"]
    lines = [
        "=" * 72, "SESSION METADATA", "=" * 72,
        f"session_id:      {session_data['session_id']}",
        f"developer:       {meta['developer']}",
        f"email:           {meta['email']}",
        f"hostname:        {meta['hostname']}",
        f"platform:        {meta['platform']}",
        f"os_user:         {meta['os_user']}",
        f"git_branch:      {meta['git_branch']}",
        f"project_dir:     {meta['project_dir']}",
        f"claude_version:  {meta['claude_version']}",
        f"message_count:   {session_data['message_count']}",
        f"start_time:      {session_data['start_time']}",
        f"end_time:        {session_data['end_time']}",
        f"uploaded_at:     {meta['uploaded_at']}",
        "=" * 72, "",
    ]
    for msg in session_data["messages"]:
        role = msg["role"].upper()
        ts = f" [{msg['timestamp']}]" if msg.get("timestamp") else ""
        lines.extend([f"[{role}]{ts}", msg["content"], "", "---", ""])
    return "\n".join(lines)


def _find_session_file(session_id: str | None, project_dir: str | None) -> Path | None:
    """Find session JSONL file. Returns None if not found."""
    base = Path(DEFAULT_SESSIONS_DIR)
    if not base.exists():
        return None

    if project_dir:
        # Look in specific project dir
        search_dirs = [base / project_dir]
    else:
        # Search all project dirs
        search_dirs = [d for d in base.iterdir() if d.is_dir()]

    for d in search_dirs:
        if session_id:
            matches = list(d.glob(f"*{session_id}*.jsonl"))
            if matches:
                return matches[0]
        else:
            # Latest session in this dir
            jsonls = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if jsonls:
                return jsonls[-1]
    return None


@mcp.tool()
def upload_context(
    session_id: str | None = None,
    project_dir: str | None = None,
    thread_id: str | None = None,
    tool_token: str | None = None,
) -> str:
    """
    Upload a Claude Code session transcript to the shared chat context store.

    This parses the session JSONL, extracts messages with metadata, and appends
    the formatted transcript to the chat store so the RLM can reason over it.

    Args:
        session_id: Specific session ID to upload. If omitted, uploads the latest session.
        project_dir: Project directory name under ~/.claude/projects/. If omitted, searches all.
        thread_id: Chat thread to append the transcript to. Defaults to 'transcripts'.
        tool_token: Optional auth token.
    """
    if not _is_authorized(tool_token):
        return "Error: unauthorized tool call."

    thread_id = (thread_id or "transcripts").strip()

    jsonl_path = _find_session_file(session_id, project_dir)
    if jsonl_path is None:
        return f"Error: no session found (session_id={session_id}, project_dir={project_dir})"

    session_data = _parse_session(jsonl_path)
    if session_data["message_count"] == 0:
        return f"Session {jsonl_path.stem} is empty, nothing to upload."

    transcript = _format_transcript(session_data)

    # Append to chat store (same backend as chat_rlm_query uses)
    store = get_chat_store(f"{thread_id}/{session_data['session_id']}.txt")
    # Write full transcript as a single turn
    store.append_turn(
        query=f"[SESSION UPLOAD] {session_data['session_id']}",
        answer=transcript,
    )

    meta = session_data["metadata"]
    return (
        f"Uploaded session {session_data['session_id']} "
        f"({session_data['message_count']} messages, "
        f"developer={meta['developer']}, branch={meta['git_branch']}) "
        f"to thread '{thread_id}'."
    )


@mcp.custom_route("/rlm", methods=["POST"])
async def rlm_http(request: Request) -> JSONResponse:
    payload = await request.json()
    query = str(payload.get("query", "")).strip()
    context = str(payload.get("context", ""))

    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)

    try:
        answer = rlm_service.answer(context=context, query=query)
    except Exception as exc:
        return JSONResponse({"error": f"Error running RLM: {exc}"}, status_code=500)

    return JSONResponse({"answer": answer})


def run_server() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
        return

    if transport in {"http", "streamable-http", "sse"}:
        mcp.run(transport="streamable-http")
        return

    raise RuntimeError(f"Unsupported MCP_TRANSPORT: {transport}")


if __name__ == "__main__":
    run_server()
