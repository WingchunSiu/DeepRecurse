# DeepRecurse

Local prototype for a shared-history chat interface where RLM execution lives in an MCP server tool.

## Run

Run MCP server (hosts RLM execution):

```bash
python DeepRecurse/claude_skill_mcp/server.py
```

Then run the local CLI client (in a separate terminal):

```bash
python DeepRecurse/main.py
```

Then chat interactively in the terminal. Type `exit` (or `quit`) to stop.

Useful client flags:

- `--chat-file` path to shared chat context log (default: `DeepRecurse/chat.txt`)

Each turn:
1. CLI sends query to MCP-hosted `chat_rlm_query` tool.
2. Server reads prior turns from `chat.txt` as context.
3. Server runs `RLM_REPL.completion(context, query)`.
4. Server appends `USER` + `ASSISTANT` entries back to the same chat file.
5. CLI prints the returned assistant response.
