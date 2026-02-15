// src/index.ts
// Public MVP Streamable-HTTP MCP gateway for Claude Code.
// - Supports initialize / notifications/initialized / ping / tools/list / tools/call
// - Responds as SSE (text/event-stream) when client Accept includes it, otherwise JSON.
// - No auth, no session enforcement (Cloudflare isolates make in-memory sessions flaky).
// - Shared context via ChatStore DO, RLM via RlmContainer DO.

export interface Env {
  CHAT_STORE: DurableObjectNamespace;
  RLM_CONTAINER: DurableObjectNamespace;
}

type JsonRpcId = string | number | null;

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id?: JsonRpcId;
  method: string;
  params?: unknown;
}

interface ToolCallParams {
  name: string;
  arguments?: {
    query?: string;
    thread_id?: string;
    transcript?: string;
    session_id?: string;
    developer?: string;
  };
}

export class ChatStore {
  constructor(private readonly state: DurableObjectState) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/read" && request.method === "GET") {
      const chat = (await this.state.storage.get<string>("chat")) ?? "";
      return Response.json({ context: chat });
    }

    if (url.pathname === "/append" && request.method === "POST") {
      const payload = (await request.json()) as { text?: string };
      const current = (await this.state.storage.get<string>("chat")) ?? "";
      const next = `${current}${payload.text ?? ""}`;
      await this.state.storage.put("chat", next);
      return Response.json({ ok: true });
    }

    return new Response("Not Found", { status: 404 });
  }
}

export class RlmContainer {
  constructor(private readonly state: DurableObjectState) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/rlm") return new Response("Not Found", { status: 404 });

    const container = this.state.container;
    if (!container) return new Response("Container not configured", { status: 500 });

    if (!container.running) container.start({ enableInternet: true });

    const port = container.getTcpPort(8000);
    const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();

    let lastError: unknown;
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        return await port.fetch(
          new Request("http://container/rlm", {
            method: request.method,
            headers: request.headers,
            body,
          }),
        );
      } catch (err) {
        lastError = err;
        await new Promise((r) => setTimeout(r, 250));
      }
    }

    return new Response(String(lastError ?? "Container failed to accept connections"), { status: 502 });
  }
}

/* ----------------------- Streamable HTTP helpers ----------------------- */

function wantsSse(request: Request): boolean {
  const accept = request.headers.get("accept") ?? "";
  return accept.toLowerCase().includes("text/event-stream");
}

function streamableResponse(request: Request, payload: unknown, status = 200): Response {
  if (!wantsSse(request)) {
    return Response.json(payload, { status });
  }

  // Single-shot SSE: one event, then close (MVP).
  const sse = `event: message\ndata: ${JSON.stringify(payload)}\n\n`;
  return new Response(sse, {
    status,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}

function jsonRpcResult(request: Request, id: JsonRpcId, result: unknown): Response {
  return streamableResponse(request, { jsonrpc: "2.0", id, result });
}

function jsonRpcError(request: Request, id: JsonRpcId, code: number, message: string): Response {
  return streamableResponse(request, { jsonrpc: "2.0", id, error: { code, message } });
}

/* ----------------------- Chat + RLM plumbing ----------------------- */

async function readContext(env: Env, threadId: string): Promise<string> {
  const id = env.CHAT_STORE.idFromName(threadId);
  const stub = env.CHAT_STORE.get(id);
  const resp = await stub.fetch("https://chat-store/read");
  if (!resp.ok) throw new Error("Failed to read chat context");
  const data = (await resp.json()) as { context?: string };
  return data.context ?? "";
}

async function appendContext(env: Env, threadId: string, text: string): Promise<void> {
  const id = env.CHAT_STORE.idFromName(threadId);
  const stub = env.CHAT_STORE.get(id);
  const resp = await stub.fetch("https://chat-store/append", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) throw new Error("Failed to append chat context");
}

async function callRlm(env: Env, context: string, query: string, thread_id: string): Promise<string> {
  // Use a single container instance name for MVP.
  const id = env.RLM_CONTAINER.idFromName("rlm");
  const stub = env.RLM_CONTAINER.get(id);

  const resp = await stub.fetch("https://rlm-container/rlm", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ context, query, thread_id }),
  });

  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`RLM backend error (${resp.status}): ${detail.slice(0, 400)}`);
  }

  const data = (await resp.json()) as { answer?: string };
  if (typeof data.answer !== "string") throw new Error("Invalid RLM response");
  return data.answer;
}

/* ----------------------- MCP handler ----------------------- */

async function handleOneRpc(request: Request, env: Env, rpc: JsonRpcRequest): Promise<Response> {
  const id = rpc.id ?? null;

  if (rpc.jsonrpc !== "2.0" || typeof rpc.method !== "string") {
    return jsonRpcError(request, id, -32600, "Invalid Request");
  }

  // Lifecycle
  if (rpc.method === "notifications/initialized") return new Response(null, { status: 202 });

  if (rpc.method === "initialize") {
    // Public + stateless initialize. (You may add Mcp-Session-Id later, but do NOT enforce it in-memory.)
    return jsonRpcResult(request, id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "deeprecurse-worker-mcp", version: "0.1.0" },
    });
  }

  if (rpc.method === "ping") return jsonRpcResult(request, id, {});

  // Compatibility no-ops
  if (rpc.method === "resources/list") return jsonRpcResult(request, id, { resources: [] });
  if (rpc.method === "prompts/list") return jsonRpcResult(request, id, { prompts: [] });

  // Tools
  if (rpc.method === "tools/list") {
    return jsonRpcResult(request, id, {
      tools: [
        {
          name: "chat_rlm_query",
          description:
            "Use this to query the Python RLM backend while reading/updating shared persistent thread context (thread_id).",
          inputSchema: {
            type: "object",
            properties: {
              query: { type: "string" },
              thread_id: { type: "string" },
            },
            required: ["query", "thread_id"],
          },
        },
        {
          name: "upload_context",
          description:
            "Upload a Claude Code session transcript to the shared context store. The transcript is stored under a thread so the RLM can reason over past sessions.",
          inputSchema: {
            type: "object",
            properties: {
              transcript: { type: "string", description: "The full session transcript text to upload." },
              session_id: { type: "string", description: "Session identifier." },
              thread_id: { type: "string", description: "Thread to store the transcript under (default: 'transcripts')." },
              developer: { type: "string", description: "Developer name/identifier." },
            },
            required: ["transcript", "session_id"],
          },
        },
      ],
    });
  }

  if (rpc.method === "tools/call") {
    const params = (rpc.params ?? {}) as ToolCallParams;

    if (params.name === "chat_rlm_query") {
      const query = params.arguments?.query?.trim();
      const threadId = params.arguments?.thread_id?.trim();
      if (!query || !threadId) {
        return jsonRpcError(request, id, -32602, "query and thread_id are required");
      }

      try {
        const context = await readContext(env, threadId);
        const answer = await callRlm(env, context, query, threadId);
        const turnText = `${context ? "\n" : ""}USER: ${query}\nASSISTANT: ${answer}\n`;
        await appendContext(env, threadId, turnText);

        return jsonRpcResult(request, id, { content: [{ type: "text", text: answer }] });
      } catch (err) {
        return jsonRpcError(
          request,
          id,
          -32000,
          err instanceof Error ? err.message : "Unknown internal error",
        );
      }
    }

    if (params.name === "upload_context") {
      const transcript = params.arguments?.transcript?.trim();
      const sessionId = params.arguments?.session_id?.trim();
      const threadId = params.arguments?.thread_id?.trim() || "transcripts";
      const developer = params.arguments?.developer || "unknown";

      if (!transcript || !sessionId) {
        return jsonRpcError(request, id, -32602, "transcript and session_id are required");
      }

      try {
        // Store under a combined key: thread_id + session_id
        const storeKey = `${threadId}/${sessionId}`;
        const turnText = `\n[SESSION UPLOAD] ${sessionId} (developer: ${developer})\n${transcript}\n`;
        await appendContext(env, storeKey, turnText);

        const msg = `Uploaded session ${sessionId} (developer=${developer}) to thread '${threadId}'.`;
        return jsonRpcResult(request, id, { content: [{ type: "text", text: msg }] });
      } catch (err) {
        return jsonRpcError(
          request,
          id,
          -32000,
          err instanceof Error ? err.message : "Unknown internal error",
        );
      }
    }

    return jsonRpcError(request, id, -32602, "Unknown tool");
  }

  return jsonRpcError(request, id, -32601, "Method not found");
}

async function handleMcp(request: Request, env: Env): Promise<Response> {
  // CORS preflight (harmless for non-browser clients)
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "POST,OPTIONS",
        "access-control-allow-headers": "content-type,accept,mcp-session-id,authorization",
      },
    });
  }

  if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });

  // Support batch JSON-RPC (array) as a compatibility bonus.
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonRpcError(request, null, -32700, "Parse error");
  }

  if (Array.isArray(body)) {
    const responses: unknown[] = [];
    for (const item of body) {
      if (typeof item !== "object" || item === null) continue;
      const rpc = item as JsonRpcRequest;

      // For batch, we must respond with JSON, not SSE, per pragmatic client expectations.
      // (If you need SSE batch later, implement a stream builder.)
      const resp = await handleOneRpc(new Request(request, { headers: { ...Object.fromEntries(request.headers) } }), env, rpc);
      const json = await resp.json().catch(() => null);
      if (json) responses.push(json);
    }
    return Response.json(responses);
  }

  return handleOneRpc(request, env, body as JsonRpcRequest);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/healthz") return new Response("ok", { status: 200 });
    if (url.pathname === "/mcp") return handleMcp(request, env);

    return new Response("Not Found", { status: 404 });
  },
};

/*
Quick checks:

# tools/list (JSON)
curl -sS https://<host>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# tools/list (SSE)
curl -N -sS https://<host>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
*/
