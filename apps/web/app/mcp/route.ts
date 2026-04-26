import { handleMcpRequest } from "@jseek/mcp-server/handler";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Accept, Mcp-Session-Id, Last-Event-ID, Mcp-Protocol-Version",
  "Access-Control-Expose-Headers": "Mcp-Session-Id",
};

function withCors(response: Response): Response {
  for (const [key, value] of Object.entries(CORS_HEADERS)) {
    response.headers.set(key, value);
  }
  return response;
}

/**
 * Best-effort extraction of the JSON-RPC method and (for `tools/call`) the
 * tool name from the request body, without consuming it. Returns nulls for
 * non-POST verbs, non-JSON bodies, or any parse error — instrumentation
 * must never break the request.
 *
 * NOTE: we intentionally do NOT log tool arguments — they may contain user
 * search queries (PII-ish freetext). Only the verb + method + tool name +
 * body byte size are recorded.
 */
async function inspectBody(
  req: Request,
): Promise<{ rpcMethod: string | null; toolName: string | null; bodyBytes: number }> {
  if (req.method !== "POST") {
    return { rpcMethod: null, toolName: null, bodyBytes: 0 };
  }
  try {
    const text = await req.clone().text();
    const bodyBytes = text.length;
    if (!text) return { rpcMethod: null, toolName: null, bodyBytes };
    const parsed: unknown = JSON.parse(text);
    // JSON-RPC body is either a single object or a batch array. Take the
    // first call's method/tool name as a representative sample — a single
    // POST nearly always carries one call.
    const first = Array.isArray(parsed) ? parsed[0] : parsed;
    if (!first || typeof first !== "object") {
      return { rpcMethod: null, toolName: null, bodyBytes };
    }
    const obj = first as Record<string, unknown>;
    const rpcMethod = typeof obj.method === "string" ? obj.method : null;
    let toolName: string | null = null;
    if (rpcMethod === "tools/call" && obj.params && typeof obj.params === "object") {
      const params = obj.params as Record<string, unknown>;
      if (typeof params.name === "string") toolName = params.name;
    }
    return { rpcMethod, toolName, bodyBytes };
  } catch {
    return { rpcMethod: null, toolName: null, bodyBytes: 0 };
  }
}

/**
 * Instrumented wrapper around `handleMcpRequest`. Emits one structured log
 * line per request prefixed with `[mcp]` for grep-ability in Vercel logs.
 *
 * `handler_duration_ms` measures time spent in the handler producing a
 * Response — for streaming/SSE replies this excludes time spent draining
 * the body to the client (which is roughly what Vercel function-time
 * billing captures anyway).
 */
async function instrument(verb: string, req: Request): Promise<Response> {
  const start = Date.now();
  const meta = await inspectBody(req);
  let status = 0;
  let error: string | null = null;
  try {
    const response = await handleMcpRequest(req);
    status = response.status;
    return withCors(response);
  } catch (err) {
    status = 500;
    error = err instanceof Error ? err.message : String(err);
    throw err;
  } finally {
    const duration_ms = Date.now() - start;
    console.info("[mcp]", {
      verb,
      rpc_method: meta.rpcMethod,
      tool: meta.toolName,
      status,
      handler_duration_ms: duration_ms,
      body_bytes: meta.bodyBytes,
      error,
    });
  }
}

export async function OPTIONS() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function POST(req: Request) {
  return instrument("POST", req);
}

export async function GET(req: Request) {
  return instrument("GET", req);
}

export async function DELETE(req: Request) {
  return instrument("DELETE", req);
}
