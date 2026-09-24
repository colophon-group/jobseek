import { NextResponse } from "next/server";

import { isLocale } from "@/lib/i18n";
import { getClientIp, queryIntentBurstLimiter, queryIntentSustainedLimiter } from "@/lib/rate-limit";
import { logExternalError } from "@/lib/safe-external-error";
import { proposeQueryFilters, QueryIntentError } from "@/lib/services/query-intent";
import { buildQueryIntentRequest } from "@/lib/search/query-intent";

const PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };

function fail(status: number, code: string, retryAfter?: number) {
  return NextResponse.json({ error: code }, {
    status,
    headers: { ...PRIVATE_HEADERS, ...(retryAfter ? { "Retry-After": String(retryAfter) } : {}) },
  });
}

export async function POST(request: Request) {
  if (process.env.SEARCH_QUERY_JEV_ENABLED !== "true") return fail(503, "disabled");
  if (request.headers.get("content-type")?.split(";")[0] !== "application/json") {
    return fail(415, "unsupported_media_type");
  }
  const contentLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > 2_048) return fail(413, "too_large");
  let input: unknown;
  try {
    const raw = await request.text();
    if (raw.length > 2_048) return fail(413, "too_large");
    input = JSON.parse(raw);
  } catch {
    return fail(400, "invalid_request");
  }
  if (!input || typeof input !== "object" || Array.isArray(input)) return fail(400, "invalid_request");
  const { query, locale } = input as Record<string, unknown>;
  if (typeof query !== "string" || typeof locale !== "string" || !isLocale(locale)) {
    return fail(400, "invalid_request");
  }
  try { buildQueryIntentRequest(query.trim(), locale); }
  catch { return fail(400, "invalid_request"); }

  const ip = getClientIp(request.headers);
  try {
    const [burst, sustained] = await Promise.all([
      queryIntentBurstLimiter.limit(ip),
      queryIntentSustainedLimiter.limit(ip),
    ]);
    if (!burst.success || !sustained.success) {
      return fail(429, "rate_limited", Math.max(1,
        Math.ceil((Math.max(burst.reset, sustained.reset) - Date.now()) / 1_000)));
    }
  } catch (error) {
    logExternalError("warn", { service: "redis", operation: "query_intent_rate_limit" }, error);
    return fail(503, "temporarily_unavailable", 30);
  }

  const started = performance.now();
  try {
    const proposal = await proposeQueryFilters({ query, locale, signal: request.signal });
    const duration = Math.round(performance.now() - started);
    return NextResponse.json(proposal, {
      headers: { ...PRIVATE_HEADERS, "Server-Timing": `query-intent;dur=${duration}` },
    });
  } catch (error) {
    if (!(error instanceof QueryIntentError)) {
      logExternalError("error", { service: "external_http", operation: "query_intent" }, error);
    }
    const code = error instanceof QueryIntentError ? error.code : "unavailable";
    return fail(code === "invalid" ? 400 : 503, code);
  }
}
