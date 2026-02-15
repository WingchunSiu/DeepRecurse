# DeepRecurse

Shared-history chat prototype where RLM execution lives in an MCP tool.

This repo now supports both:
- local stdio MCP execution
- cloud deployment with a Python MCP backend + Cloudflare Worker gateway

## 1) Local development

Install dependencies:

```bash
cd /Users/ryanhe/Ryan/TreeHacks/DeepRecurse
python -m pip install -r requirements.txt
```

Run MCP server (stdio mode):

```bash
python claude_tool_mcp/server.py
```

Add to Claude Code MCP list:

```bash
claude mcp add deeprecurse --transport stdio -- \
  python /Users/ryanhe/Ryan/TreeHacks/DeepRecurse/claude_tool_mcp/server.py
```

Run local CLI client in another terminal:

```bash
python main.py
```

Useful flag:
- `--chat-file` path to shared chat context log (default: `chat.txt`)

## 2) Cloud backend (Python MCP in container)

The server supports:
- `MCP_TRANSPORT=stdio` (local default)
- `MCP_TRANSPORT=streamable-http` (remote HTTP endpoint)
- `CHAT_STORE_BACKEND=file|r2`

### Required environment variables (cloud)

- `OPENAI_API_KEY`
- `MCP_TRANSPORT=streamable-http`
- `MCP_HTTP_PATH=/mcp`
- `PORT=8000`

If using R2-backed chat history:
- `CHAT_STORE_BACKEND=r2`
- `R2_BUCKET`
- `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_REGION` (optional, default `auto`)

Optional tool-level guard:
- `MCP_TOOL_TOKEN` (if set, callers must include `tool_token` argument in `chat_rlm_query`)

### Build and run container locally

```bash
docker build -t deeprecurse-mcp:latest .
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=... \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HTTP_PATH=/mcp \
  deeprecurse-mcp:latest
```

## 3) Cloudflare Worker gateway

Worker gateway code is in:
- `cloudflare/worker-gateway`

It provides:
- `GET /healthz`
- `POST /mcp` proxy to backend MCP URL
- bearer auth at the edge via `MCP_GATEWAY_TOKEN`

### Configure + deploy Worker

```bash
cd /Users/ryanhe/Ryan/TreeHacks/DeepRecurse/cloudflare/worker-gateway
npm install
npx wrangler secret put MCP_GATEWAY_TOKEN
# edit wrangler.toml var BACKEND_MCP_URL to your container URL ending in /mcp
npx wrangler deploy
```

## 4) Connect Claude Code to remote MCP

After Worker deploy, connect using your Worker URL:

```bash
claude mcp add --transport http deeprecurse https://<your-worker-domain>/mcp
```

If your Claude Code version supports custom headers, include:
- `Authorization: Bearer <MCP_GATEWAY_TOKEN>`

## Request flow

1. Claude calls Worker at `/mcp`.
2. Worker validates bearer token and proxies to backend MCP server.
3. Backend tool `chat_rlm_query` loads shared context (file or R2).
4. Backend runs `RLM_REPL.completion(context, query)`.
5. Backend appends USER/ASSISTANT turn and returns answer.
