# RLM with Modal Sandboxes & Parallel Sub-LLM Pool

RLM (Recursive Language Model) implementation that runs sub-LLM queries inside **persistent Modal sandboxes**, with optional **parallel execution** across a pool of sandboxes.

## Architecture

```
Root LM (gpt-5)
  │
  ├─ llm_query(prompt)           → single PersistentSandboxSubRLM
  │                                 (one Modal sandbox, reused across calls)
  │
  └─ parallel_llm_query(prompts) → SandboxPool (N sandboxes, ThreadPoolExecutor)
                                    (created lazily on first call, round-robin dispatch)
```

- **`llm_query`** — single sandbox, no pool overhead. Good for sequential sub-LLM calls.
- **`parallel_llm_query`** — pool of N sandboxes, dispatches prompts concurrently. Good when you have many independent sub-LLM calls (e.g. chunk-and-query patterns).

The pool is **lazy** — sandboxes are only created when `parallel_llm_query` is first called. If the model never calls it, no pool is created.

## Setup

```bash
pip install -r requirements.txt
# Set your OpenAI API key
echo "OPENAI_API_KEY=sk-..." > .env
# Make sure you're authenticated with Modal
modal setup
```

## Usage

### Mode 1: Local (no Modal)

```bash
python main.py --mode local --num-lines 10000
```

Sub-LLM calls run locally via OpenAI API. `parallel_llm_query` falls back to sequential.

### Mode 2: Modal sandbox (single)

```bash
python main.py --mode modal --num-lines 100000 --env-file .env
```

Sub-LLM calls run inside a persistent Modal sandbox. The model decides its own strategy — may use `llm_query`, `parallel_llm_query`, or neither (e.g. regex).

### Mode 3: Modal sandbox with forced parallel

```bash
python main.py --mode modal --force-parallel --pool-size 4 --num-lines 100000 --env-file .env
```

Uses an alternative system prompt that **forces** the model to chunk the context and use `parallel_llm_query` for all sub-LLM calls. Useful for testing/benchmarking the pool.

## CLI Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `--mode` | `local` | `local` or `modal` |
| `--num-lines` | `100000` | Haystack size for NIAH test |
| `--model` | `gpt-5` | Root LM model |
| `--recursive-model` | `gpt-5-nano` | Sub-LLM model |
| `--max-iterations` | `10` | Max root LM iterations |
| `--pool-size` | `4` | Number of parallel sandboxes |
| `--force-parallel` | off | Force parallel_llm_query via system prompt |
| `--env-file` | none | Path to .env for sandbox secrets |

## How the Parallel Pool Works

1. On first `parallel_llm_query()` call, `SandboxPool` creates N Modal sandboxes **concurrently** (~1-2s wall time regardless of N)
2. Prompts are dispatched **round-robin** across sandboxes via `ThreadPoolExecutor`
3. Each sandbox has a lock — one prompt at a time per sandbox
4. If a sandbox errors, it's recreated and the prompt retried once
5. Results returned in same order as input prompts
6. All sandboxes terminated on cleanup

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | CLI entry point (NIAH test) |
| `rlm/repl.py` | REPL environment, `PersistentSandboxSubRLM`, `REPLEnv` with `parallel_llm_query` |
| `rlm/sandbox_pool.py` | `SandboxPool` — parallel sandbox management |
| `rlm/rlm_repl.py` | `RLM_REPL` — root LM loop |
| `rlm/sub_rlm_worker.py` | Python script that runs inside each Modal sandbox |
| `rlm/utils/prompts.py` | System prompts (default + force-parallel variant) |

## Example Run (force-parallel, 100K lines)

```
[SandboxPool] Creating 4 sandboxes in parallel...
[SandboxPool] 4 sandboxes ready in 1.1s
[SandboxPool] Dispatching 78 prompts across 4 sandboxes
[SandboxPool] All 78 prompts completed in 148.8s
[SandboxPool] Dispatching 3 prompts across 4 sandboxes   # verification pass
[SandboxPool] All 3 prompts completed in 7.0s

Result:   3091632
Expected: 3091632
Match:    True
```
