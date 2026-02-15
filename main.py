"""Local multi-turn chat CLI that queries the MCP server tool."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_RECURSIVE_MODEL = "gpt-5-nano"
DEFAULT_CHAT_FILE = "chat.txt"
EXIT_COMMANDS = {"exit", "quit", ":q"}


def project_root() -> Path:
    return Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype chat CLI using MCP-hosted RLM")
    parser.add_argument("--chat-file", default=DEFAULT_CHAT_FILE, help="Shared chat log path")
    return parser.parse_args()


@dataclass
class ChatConfig:
    chat_path: Path
    server_module: str = "claude_tool_mcp.server"


class MCPChatClient:
    """Thin local client that invokes the MCP server's chat tool function."""

    def __init__(self, server_module: str, chat_path: Path):
        # Import is local to keep CLI startup lightweight.
        from importlib import import_module

        server = import_module(server_module)
        self._chat_tool = server.chat_rlm_query
        self._chat_path = chat_path

    def answer(self, query: str) -> str:
        # chat_rlm_query handles read context + generate + append in server.py
        return self._chat_tool(query=query, chat_file=str(self._chat_path))


class ChatSession:
    def __init__(self, client: MCPChatClient):
        self.client = client

    def run(self) -> None:
        print("Chat-RLM ready. Type your question, or 'exit' to quit.")
        while True:
            query = input("You: ").strip()
            if not query:
                continue
            if query.lower() in EXIT_COMMANDS:
                print("Goodbye!")
                break

            print("Generating answer via MCP server tool...")
            answer = self.client.answer(query=query)
            print(f"Assistant: {answer}")


def build_config(args: argparse.Namespace) -> ChatConfig:
    chat_path = Path(args.chat_file)
    if not chat_path.is_absolute():
        chat_path = project_root() / chat_path

    return ChatConfig(
        chat_path=chat_path,
    )


def main() -> None:
    args = parse_args()
    config = build_config(args)

    session = ChatSession(MCPChatClient(config.server_module, config.chat_path))
    session.run()


if __name__ == "__main__":
    main()
