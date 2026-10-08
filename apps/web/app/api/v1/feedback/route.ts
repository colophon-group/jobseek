import { isIP } from "node:net";
import { NextResponse, type NextRequest } from "next/server";
import { feedbackSubmissionSchema, MAX_FEEDBACK_BYTES } from "@jseek/mcp-server/feedback-contract";
import { MCP_SERVER_VERSION } from "@jseek/mcp-server/metadata";
import { db } from "@/db";
import { mcpFeedback } from "@/db/schema";
import { feedbackLimiter, getClientIp } from "@/lib/rate-limit";
import { publicApiConsumerFor, withPublicApiObservability } from "@/lib/public-api-observability";
import { logExternalError } from "@/lib/safe-external-error";

const HEADERS = {
  "Cache-Control": "private, no-store",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
function fail(status: number, error: string, retryAfter?: number) {
  return NextResponse.json({ success: false, error }, {
    status, headers: { ...HEADERS, ...(retryAfter ? { "Retry-After": String(retryAfter) } : {}) },
  });
}

async function handlePost(request: NextRequest) {
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
    return fail(415, "unsupported_media_type");
  }
  if (Number(request.headers.get("content-length")) > MAX_FEEDBACK_BYTES) return fail(413, "too_large");
  // Enforce the actual byte limit even when Content-Length is missing or false.
  let input: unknown;
  try {
    const reader = request.body?.getReader();
    if (!reader) return fail(400, "invalid_submission");
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_FEEDBACK_BYTES) {
        await reader.cancel();
        return fail(413, "too_large");
      }
      chunks.push(value);
    }
    input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return fail(400, "invalid_submission");
  }
  const parsed = feedbackSubmissionSchema.safeParse(input);
  if (!parsed.success) return fail(400, "invalid_submission");

  // Only authenticated hosted provenance may forward a client IP. Never persist
  // it in feedback, return it, or accept an anonymous caller's forwarded value.
  const consumer = publicApiConsumerFor(request);
  const forwardedIp = request.headers.get("x-jobseek-mcp-client-ip");
  const clientIp = consumer === "hosted_mcp" && forwardedIp && isIP(forwardedIp)
    ? forwardedIp : getClientIp(request.headers);
  try {
    const limit = await feedbackLimiter.limit(clientIp);
    if (!limit.success) return fail(429, "rate_limited", Math.max(1, Math.ceil((limit.reset - Date.now()) / 1000)));
  } catch (error) {
    logExternalError("warn", { service: "redis", operation: "feedback_rate_limit" }, error);
    return fail(503, "temporarily_unavailable", 30);
  }

  try {
    const { kind, ...payload } = parsed.data;
    await db.insert(mcpFeedback).values({ kind, payload, serverVersion: MCP_SERVER_VERSION, consumer });
    return NextResponse.json({ success: true }, { headers: HEADERS });
  } catch (error) {
    logExternalError("error", { service: "database", operation: "feedback_submit" }, error);
    return fail(503, "temporarily_unavailable");
  }
}

export const POST = withPublicApiObservability("feedback", handlePost);
export function OPTIONS() {
  return new Response(null, { status: 204, headers: HEADERS });
}
