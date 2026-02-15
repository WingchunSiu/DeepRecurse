"""MCP server that executes Chat-RLM calls against a shared Modal volume context."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RLM_DIR = PROJECT_ROOT / "rlm"
if str(RLM_DIR) not in sys.path:
    sys.path.insert(0, str(RLM_DIR))

from modal_runtime import app, run_rlm_remote


DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_RECURSIVE_MODEL = "gpt-5-nano"
DEFAULT_CHAT_FILE = "chat.txt"
DEFAULT_MAX_ITERATIONS = 10
DEFAULT_CONTEXT_RELPATH = "runs/0fad4ca550eb4d818b81a00c0f897218/context.txt"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class RLMConfig:
    model: str = DEFAULT_MODEL
    recursive_model: str = DEFAULT_RECURSIVE_MODEL
    max_iterations: int = DEFAULT_MAX_ITERATIONS


class RLMService:
    def __init__(self, config: RLMConfig):
        self.config = config

    def answer(self, query: str) -> str:
        with app.run():
            return run_rlm_remote.remote(
                query=query,
                context_relpath=DEFAULT_CONTEXT_RELPATH,
                model=self.config.model,
                recursive_model=self.config.recursive_model,
                max_iterations=self.config.max_iterations,
            )


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
    _ = chat_file  # preserved for backward-compatible tool signature

    try:
        answer = rlm_service.answer(query=clean_query)
    except Exception as exc:
        return f"Error running RLM: {exc}"

    return answer


@mcp.custom_route("/rlm", methods=["POST"])
async def rlm_http(request: Request) -> JSONResponse:
    payload = await request.json()
    query = str(payload.get("query", "")).strip()

    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)

    try:
        answer = rlm_service.answer(query=query)
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
