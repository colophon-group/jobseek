import { type NextRequest, NextResponse } from "next/server";
import { apiLimiter, getClientIp } from "@/lib/rate-limit";
import { CACHE_TTL_MEDIUM } from "@/lib/cache-ttl";
import { siteConfig } from "@/content/config";
import { logExternalError } from "@/lib/safe-external-error";
import {
  defaultLocale,
  isLocale,
  locales,
  type Locale,
} from "@/lib/i18n";
import type { ParsedSearchFilters } from "@/lib/services/search-input";
import {
  PUBLIC_SEARCH_QUERY_PARAMETERS,
  SEARCH_EMPLOYMENT_TYPE_VALUES,
  SEARCH_WORK_MODE_VALUES,
} from "@jseek/mcp-server/public-api-contract";

/** Rate-limit result to thread through to non-cacheable API responses. */
export type RateLimitInfo = { limit: number; remaining: number; reset: number };

/** Check rate limit and return 429 response if exceeded. */
export async function checkRateLimit(
  request: NextRequest,
): Promise<NextResponse | RateLimitInfo | null> {
  const ip = getClientIp(request.headers);
  try {
    const { success, limit, remaining, reset } = await apiLimiter.limit(ip);
    if (!success) {
      const retryAfter = Math.ceil((reset - Date.now()) / 1000);
      return NextResponse.json(
        { error: "Too many requests" },
        {
          status: 429,
          headers: {
            "Retry-After": String(Math.max(1, retryAfter)),
            "X-RateLimit-Limit": String(limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": String(reset),
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
          },
        },
      );
    }
    return { limit, remaining, reset };
  } catch (err) {
    // Redis unavailable — allow request through. Log so a sustained Redis
    // outage (which silently disables rate-limiting on every public
    // `/api/v1/*` request) is visible in Vercel/Loki rather than looking
    // like normal "no-rate-limit-headers" traffic. See #3175.
    // The stable `external_client_error` event and `public_api_rate_limit`
    // operation are queryable in Loki.
    logExternalError("warn", { service: "redis", operation: "public_api_rate_limit" }, err);
  }
  return null;
}

/** Build a private JSON response with standard API headers.
 *
 * Use this for contract errors, provider failures, rate limits, and any other
 * caller-specific response. Successful deterministic GET responses must use
 * `sharedApiResponse()` so per-client rate-limit metadata can never enter a
 * shared cache.
 */
export function apiResponse(
  data: unknown,
  options?: {
    rateLimit?: RateLimitInfo | null;
    status?: number;
  },
): NextResponse {
  const headers: Record<string, string> = {
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET",
  };
  if (options?.rateLimit) {
    headers["X-RateLimit-Limit"] = String(options.rateLimit.limit);
    headers["X-RateLimit-Remaining"] = String(options.rateLimit.remaining);
    headers["X-RateLimit-Reset"] = String(options.rateLimit.reset);
  }
  return NextResponse.json(data, { headers, status: options?.status });
}

/** Build a deterministic public JSON response cached only by Vercel's CDN.
 *
 * Browsers and downstream intermediaries must revalidate. Vercel consumes and
 * strips `Vercel-CDN-Cache-Control`, so the public response never advertises a
 * reusable shared TTL or contains caller-specific rate-limit metadata. A
 * pre-cache Vercel Firewall rule enforces the public API request budget even
 * when this response is served without invoking the origin function.
 */
export function sharedApiResponse(
  data: unknown,
  options?: { maxAge?: number },
): NextResponse {
  const maxAge = options?.maxAge ?? CACHE_TTL_MEDIUM;
  return NextResponse.json(data, {
    headers: {
      "Cache-Control": "public, max-age=0, must-revalidate",
      "Vercel-CDN-Cache-Control": `public, max-age=${maxAge}`,
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET",
    },
  });
}

/** Parse and validate the response locale shared by every public v1 route. */
export function parseApiLocale(
  params: URLSearchParams,
  rateLimit?: RateLimitInfo | null,
): Locale | NextResponse {
  const locale = params.get("locale") ?? defaultLocale;
  if (isLocale(locale)) return locale;

  const response = apiResponse(
    { error: `Invalid 'locale' param. Supported: ${locales.join(", ")}` },
    { rateLimit, status: 400 },
  );
  return response;
}

/** Reject unsupported finite-list filters at the public API boundary. */
export function validatePublicEnumListParam(
  name: "wm" | "etype",
  raw: string | undefined,
  supported: readonly string[],
  rateLimit?: RateLimitInfo | null,
): NextResponse | null {
  if (raw === undefined) return null;
  const values = raw.split(",").map((value) => value.trim());
  const invalid = values.filter(
    (value) => value.length === 0 || !supported.includes(value),
  );
  if (invalid.length === 0) return null;

  const detail = invalid.filter(Boolean).join(", ") || "empty value";
  const response = apiResponse(
    {
      error: `Invalid '${name}' value(s): ${detail}. Supported: ${supported.join(", ")}`,
    },
    { rateLimit, status: 400 },
  );
  return response;
}

/** Reject unresolved exact-slug filters instead of silently widening a public search. */
export function validateResolvedPublicFilters(
  parsed: Pick<ParsedSearchFilters, "unresolvedExplicitSlugs">,
  rateLimit?: RateLimitInfo | null,
): NextResponse | null {
  const unresolved = parsed.unresolvedExplicitSlugs;
  if (!unresolved) return null;

  for (const name of ["loc", "occ", "sen", "tech"] as const) {
    const values = unresolved[name];
    if (!values?.length) continue;
    const response = apiResponse(
      {
        error: `Invalid '${name}' slug(s): ${values.join(", ")}. Use /api/v1/resolve for exact slugs.`,
      },
      { rateLimit, status: 400 },
    );
    return response;
  }
  return null;
}

/** Build the full URL to the site for a given path. */
export function siteUrl(path: string): string {
  return `${siteConfig.url}${path}`;
}

/** Reconstruct explore page URL from query params (for moreAt links). */
export function exploreUrl(
  params: URLSearchParams,
  locale: string = "en",
): string {
  const kept = new URLSearchParams();
  for (const key of PUBLIC_SEARCH_QUERY_PARAMETERS) {
    if (key === "locale") continue;
    const val = params.get(key);
    if (val) kept.set(key, val);
  }
  // REST defaults to all document languages, whereas Explore normally uses
  // the viewer's preference. Carry the UI's existing `*` sentinel so the
  // linked result set stays equivalent when the API caller omitted `lang`.
  if (!kept.has("lang")) kept.set("lang", "*");
  // Public search salary bounds are always EUR. Pin the UI link so a user's
  // display-currency preference cannot reinterpret the same numeric range.
  if (kept.has("sal")) kept.set("salcur", "EUR");
  const qs = kept.toString();
  return siteUrl(`/${locale}/explore${qs ? `?${qs}` : ""}`);
}

export const PUBLIC_WORK_MODE_VALUES = SEARCH_WORK_MODE_VALUES;
export const PUBLIC_EMPLOYMENT_TYPE_VALUES = SEARCH_EMPLOYMENT_TYPE_VALUES;
