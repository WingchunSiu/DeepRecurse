# DeepRecurse

**RLM-as-a-service: persistent, recursive reasoning for AI coding agents.**

Built on top of [alexzhang13/rlm](https://github.com/alexzhang13/rlm) — the open-source [Recursive Language Model](https://arxiv.org/abs/2512.24601v1) framework where LLMs offload context into a REPL environment and recursively call sub-LLMs to decompose complex tasks. On benchmarks like OOLONG (132k tokens), RLM(GPT-5-mini) outperforms GPT-5 by over 34 points at similar cost.

DeepRecurse takes the core RLM and turns it into **deployed infrastructure** that AI agents can call as a tool. We wrap the RLM in an [MCP](https://modelcontextprotocol.io) server, deploy the compute on [Modal](https://modal.com) serverless, and add a persistent memory layer (Modal Volume) so the RLM accumulates context across sessions. The result: plug it into Claude Code and the agent gains the ability to recursively reason over arbitrarily large contexts — and remember what it learned.

## What We Added on Top of RLM

| Layer | What | Why |
|-------|------|-----|
| **MCP server** | `server.py` (stdio) + Cloudflare Worker (HTTP) | Exposes RLM as tools any MCP-compatible agent can call |
| **Modal backend** | `modal_runtime.py` — serverless functions + HTTP endpoints | No infra to manage; scales to zero when idle |
| **Persistent memory** | Modal Volume stores `{thread_id}/context.txt` | RLM builds on past sessions instead of starting from scratch |
| **Session auto-upload** | Claude Code `Stop` hook captures full transcripts | Every conversation becomes searchable context for the RLM |
| **Modal Sandbox sub-LLMs** | `ModalSandboxSubRLM` runs sub-LLM calls in isolated sandboxes | Safe code execution for recursive calls in the cloud |
| **CLI tools** | `python -m deeprecurse.query` / `store` | Use RLM outside of Claude Code |

## Architecture

```
Claude Code
  │
  └─ MCP (stdio or streamable-http)
      │
      ▼
MCP Server                              ← thin routing layer
  │  (Python stdio server OR Cloudflare Worker)
  │
  ├─ chat_rlm_query(query, thread_id)
  │     │
  │     ▼
  │   Modal: run_rlm_remote()
  │     ├─ reads context from Volume: /{thread_id}/context.txt
  │     ├─ runs RLM_REPL reasoning loop:
  │     │    root LLM (gpt-5) ──writes code──▶ sandboxed REPL
  │     │                                        │
  │     │    REPL calls llm_query() ────────▶ sub-LLM (gpt-5-nano)
  │     │                                        │
  │     │    results flow back to root LLM ◀─────┘
  │     │    ... repeat up to N iterations
  │     ├─ appends Q&A turn to Volume
  │     └─ returns answer
  │
  └─ upload_context(transcript, session_id, thread_id)
        │
        ▼
      Modal: store_context()
        └─ appends transcript to Volume: /{thread_id}/context.txt
```

## How RLM Reasoning Works

The RLM never sees the full context directly. Instead it interacts with it programmatically through a REPL:

1. **Recon** — the root LLM reads the context file, checks its size, identifies the format and natural chunk boundaries
2. **Filter + Analyze** — writes Python code to split the context along those boundaries, uses regex/keywords to find relevant sections, then calls `llm_query()` to delegate semantic analysis of each section to a sub-LLM
3. **Aggregate + Answer** — synthesizes sub-LLM results via a final `llm_query()` call and returns the answer

The root LLM uses a powerful model (gpt-5) for orchestration while sub-LLMs use cheaper models (gpt-5-nano) for focused analysis — keeping cost low while handling arbitrarily large contexts.

## Quick Start

### Prerequisites

- Python 3.12+
- [Modal](https://modal.com) account
- OpenAI API key
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

### 1. Set up Modal

```bash
git clone https://github.com/MichaelXiaoKun/DeepRecurse.git
cd DeepRecurse
modal token set
```

### 2. Upload your OpenAI key to the Modal Volume

```bash
modal volume create rlm-shared-volume
echo "OPENAI_API_KEY=sk-..." > /tmp/.env
modal volume put rlm-shared-volume /tmp/.env .env
rm /tmp/.env
```

### 3. Deploy the backend

```bash
cd mcp-modal
pip install -r requirements.txt
modal deploy modal_runtime.py
```

### 4. Connect to Claude Code

**Local mode (stdio — recommended for dev):**

```bash
claude mcp add deeprecurse --transport stdio -- python /path/to/DeepRecurse/mcp-modal/server.py
```

**Cloud mode (Cloudflare Worker → Modal HTTP):**

```bash
cd mcp-modal/cloudflare/worker-gateway
# set MODAL_BACKEND_URL in wrangler.toml
npm install && npm run deploy

claude mcp add deeprecurse --transport http \
  --url https://deeprecurse-mcp-modal.<subdomain>.workers.dev/mcp
```

### 5. Use it

Open Claude Code — the `chat_rlm_query` and `upload_context` tools are available automatically. The RLM handles recursive reasoning; Claude Code handles everything else.

## MCP Tools

### `chat_rlm_query`

Query the RLM with persistent thread context.

| Param | Type | Description |
|-------|------|-------------|
| `query` | string | The question to ask |
| `thread_id` | string | Thread identifier — context accumulates per thread |

### `upload_context`

Upload a transcript to the RLM's persistent memory.

| Param | Type | Description |
|-------|------|-------------|
| `transcript` | string | Full transcript text |
| `session_id` | string | Session identifier |
| `thread_id` | string | Thread to store under (default: `transcripts`) |

## Auto Session Upload

Add to `.claude/settings.local.json` to automatically capture every Claude Code session:

```json
{
  "hooks": {
    "Stop": [{
      "type": "command",
      "command": "/path/to/DeepRecurse/scripts/session_end_upload.sh"
    }]
  }
}
```

Each transcript is uploaded with metadata (developer, git branch, timestamps, message count) so the RLM can reason over your full development history.

## Project Structure

```
DeepRecurse/
├── mcp-modal/                  # MCP + Modal deployment layer
│   ├── server.py               # MCP server (stdio)
│   ├── modal_runtime.py        # Modal functions + HTTP endpoints
│   ├── rlm/                    # RLM package (mounted into Modal image)
│   └── cloudflare/             # Cloudflare Worker gateway
├── rlm/                        # Core RLM (forked from alexzhang13/rlm)
│   ├── rlm/
│   │   ├── rlm_repl.py         # RLM_REPL — recursive reasoning loop
│   │   ├── repl.py             # Sandboxed REPL with llm_query()
│   │   ├── sub_rlm_worker.py   # Sub-LLM worker for Modal Sandboxes
│   │   └── utils/
│   │       ├── llm.py          # OpenAI client wrapper
│   │       └── prompts.py      # System prompts + 3-phase strategy
│   └── main.py                 # Needle-in-haystack example
├── deeprecurse/                # CLI entry points
│   ├── query.py                # python -m deeprecurse.query
│   └── store.py                # python -m deeprecurse.store
└── scripts/
    └── session_end_upload.sh   # Stop hook for auto-upload
```

## References

- [Recursive Language Models](https://arxiv.org/abs/2512.24601v1) — Zhang, Kraska & Khattab (2025)
- [RLM blog post](https://alexzhang13.github.io/blog/2025/rlm/) and [original codebase](https://github.com/alexzhang13/rlm)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [Modal](https://modal.com)

## Built at TreeHacks 2026
