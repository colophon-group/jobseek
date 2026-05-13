import { type NextRequest, NextResponse } from "next/server";
import { apiLimiter, getClientIp } from "@/lib/rate-limit";
import { siteConfig } from "@/content/config";

/** Rate-limit result to thread through to apiResponse(). */
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
          },
        },
      );
    }
    return { limit, remaining, reset };
  } catch {
    // Redis unavailable — allow request through
  }
  return null;
}

/** Build a JSON response with standard headers. */
export function apiResponse(
  data: unknown,
  options?: { maxAge?: number; rateLimit?: RateLimitInfo | null },
): NextResponse {
  const maxAge = options?.maxAge ?? 300;
  const headers: Record<string, string> = {
    "Cache-Control": `public, max-age=${maxAge}, s-maxage=${maxAge}`,
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET",
  };
  if (options?.rateLimit) {
    headers["X-RateLimit-Limit"] = String(options.rateLimit.limit);
    headers["X-RateLimit-Remaining"] = String(options.rateLimit.remaining);
    headers["X-RateLimit-Reset"] = String(options.rateLimit.reset);
  }
  return NextResponse.json(data, { headers });
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
  for (const key of ["q", "loc", "occ", "sen", "tech", "sal", "salcur", "exp"]) {
    const val = params.get(key);
    if (val) kept.set(key, val);
  }
  const qs = kept.toString();
  return siteUrl(`/${locale}/explore${qs ? `?${qs}` : ""}`);
}
