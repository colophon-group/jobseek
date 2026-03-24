import { type NextRequest, NextResponse } from "next/server";
import { apiLimiter } from "@/lib/rate-limit";
import { siteConfig } from "@/content/config";

/** Extract client IP from request headers. */
export function getClientIp(request: NextRequest): string {
  return (
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown"
  );
}

/** Check rate limit and return 429 response if exceeded. */
export async function checkRateLimit(
  request: NextRequest,
): Promise<NextResponse | null> {
  const ip = getClientIp(request);
  try {
    const { success, reset } = await apiLimiter.limit(ip);
    if (!success) {
      const retryAfter = Math.ceil((reset - Date.now()) / 1000);
      return NextResponse.json(
        { error: "Too many requests" },
        {
          status: 429,
          headers: {
            "Retry-After": String(Math.max(1, retryAfter)),
            "Cache-Control": "no-store",
          },
        },
      );
    }
  } catch {
    // Redis unavailable — allow request through
  }
  return null;
}

/** Build a JSON response with standard headers. */
export function apiResponse(
  data: unknown,
  options?: { maxAge?: number },
): NextResponse {
  const maxAge = options?.maxAge ?? 300;
  return NextResponse.json(data, {
    headers: {
      "Cache-Control": `public, max-age=${maxAge}, s-maxage=${maxAge}`,
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET",
    },
  });
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
