import { handleMcpRequest } from "@jseek/mcp-server/handler";
import { after } from "next/server";
import { recordPublicApiMetric } from "@/lib/public-api-metrics";
import { getClientIp } from "@/lib/rate-limit";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Accept, Mcp-Session-Id, Last-Event-ID, Mcp-Protocol-Version",
  "Access-Control-Expose-Headers": "Mcp-Session-Id",
};

const MCP_RPC_METHODS = new Set([
  "initialize",
  "notifications/initialized",
  "notifications/cancelled",
  "ping",
  "tools/list",
  "tools/call",
  "resources/list",
  "resources/templates/list",
  "resources/read",
]);

const JOBSEEK_TOOL_NAMES = new Set([
  "create_watchlist_link",
  "get_ghost_analysis",
  "get_job_detail",
  "list_taxonomies",
  "resolve_slugs",
  "search_companies",
  "search_jobs",
  "trigger_batch_ghost_analysis",
  "trigger_ghost_analysis",
]);

type McpVerb = "POST" | "GET" | "DELETE" | "OPTIONS";

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
  // Read the body first so bodyBytes is preserved even when JSON.parse
  // later fails — a malformed body is itself a useful observability
  // signal.
  let text: string;
  try {
    text = await req.clone().text();
  } catch {
    return { rpcMethod: null, toolName: null, bodyBytes: 0 };
  }
  const bodyBytes = text.length;
  if (!text) return { rpcMethod: null, toolName: null, bodyBytes };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { rpcMethod: null, toolName: null, bodyBytes };
  }
  // JSON-RPC body is either a single object or a batch array. Take the
  // first call's method/tool name as a representative sample — a single
  // POST nearly always carries one call.
  const first = Array.isArray(parsed) ? parsed[0] : parsed;
  if (!first || typeof first !== "object") {
    return { rpcMethod: null, toolName: null, bodyBytes };
  }
  const obj = first as Record<string, unknown>;
  const rawRpcMethod = typeof obj.method === "string" ? obj.method : null;
  const rpcMethod =
    rawRpcMethod === null
      ? null
      : MCP_RPC_METHODS.has(rawRpcMethod)
        ? rawRpcMethod
        : "unknown";
  let toolName: string | null = null;
  if (
    rpcMethod === "tools/call" &&
    obj.params &&
    typeof obj.params === "object"
  ) {
    const params = obj.params as Record<string, unknown>;
    if (typeof params.name === "string") {
      toolName = JOBSEEK_TOOL_NAMES.has(params.name) ? params.name : "unknown";
    }
  }
  return { rpcMethod, toolName, bodyBytes };
}

async function invokeMcpHandler(req: Request): Promise<Response> {
  // Production rollout: configure the same high-entropy protected value for
  // HOSTED_MCP_API_PROVENANCE_TOKEN before deploying both sides. If absent,
  // calls remain public and are deliberately classified as external.
  const internalMcpToken = process.env.HOSTED_MCP_API_PROVENANCE_TOKEN;
  return internalMcpToken
    ? handleMcpRequest(req, undefined, { internalMcpToken })
    : handleMcpRequest(req);
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
async function instrument(
  verb: McpVerb,
  req: Request,
  invoke: () => Promise<Response>,
): Promise<Response> {
  const start = Date.now();
  const meta = await inspectBody(req);
  let status = 0;
  let error: "handler_error" | null = null;
  try {
    const response = await invoke();
    status = response.status;
    return withCors(response);
  } catch (caught) {
    status = 500;
    error = "handler_error";
    throw caught;
  } finally {
    const duration_ms = Math.min(300_000, Math.max(0, Date.now() - start));
    try {
      after(async () => {
        try {
          console.info("[mcp]", {
            verb,
            rpc_method: meta.rpcMethod,
            tool: meta.toolName,
            status,
            handler_duration_ms: duration_ms,
            body_bytes: meta.bodyBytes,
            error,
          });
        } catch {
          // Telemetry is best-effort and must never change the MCP response.
        }

        try {
          let clientIp: string | null = null;
          try {
            clientIp = getClientIp(req.headers);
          } catch {
            // Keep the aggregate metric even if proxy headers are malformed.
          }
          await recordPublicApiMetric({
            interface: "mcp",
            route: "mcp",
            consumer: "external",
            statusCode: status,
            durationMs: duration_ms,
            rateLimited: status === 429,
            clientIp,
          });
        } catch {
          // Metrics are best-effort and must never change the MCP response.
        }
      });
    } catch {
      // `after()` registration itself is non-critical telemetry work.
    }
  }
}

export async function OPTIONS(req: Request) {
  return instrument("OPTIONS", req, async () =>
    new Response(null, { status: 204, headers: CORS_HEADERS }),
  );
}

export async function POST(req: Request) {
  return instrument("POST", req, () => invokeMcpHandler(req));
}

export async function GET(req: Request) {
  return instrument("GET", req, () => invokeMcpHandler(req));
}

export async function DELETE(req: Request) {
  return instrument("DELETE", req, () => invokeMcpHandler(req));
}
