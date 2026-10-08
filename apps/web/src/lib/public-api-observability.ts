import { timingSafeEqual } from "node:crypto";
import { after, type NextRequest } from "next/server";
import { recordPublicApiMetric } from "@/lib/public-api-metrics";
import type { PublicApiMetricInput } from "@/lib/public-api-metrics-contract";
import { createSearchMeasurement } from "@/lib/search/latency";
import { getClientIp } from "@/lib/rate-limit";

const INTERNAL_MCP_TOKEN_HEADER = "x-jobseek-internal-mcp-token";
const MAX_DURATION_MS = 300_000;

export type PublicApiRestRoute = Exclude<
  PublicApiMetricInput["route"],
  "mcp" | "unknown"
>;

type RestHandler = (request: NextRequest) => Promise<Response>;

function boundedDurationMs(startedAt: number): number {
  const elapsed = Date.now() - startedAt;
  if (!Number.isFinite(elapsed)) return 0;
  return Math.min(MAX_DURATION_MS, Math.max(0, Math.round(elapsed)));
}

function statusClass(statusCode: number): "2xx" | "3xx" | "4xx" | "5xx" {
  if (statusCode >= 200 && statusCode < 300) return "2xx";
  if (statusCode >= 300 && statusCode < 400) return "3xx";
  if (statusCode >= 400 && statusCode < 500) return "4xx";
  return "5xx";
}

export function publicApiConsumerFor(request: Request): PublicApiMetricInput["consumer"] {
  try {
    const expected = process.env.HOSTED_MCP_API_PROVENANCE_TOKEN;
    const provided = request.headers.get(INTERNAL_MCP_TOKEN_HEADER);
    if (!expected || !provided) return "external";

    const expectedBytes = Buffer.from(expected, "utf8");
    const providedBytes = Buffer.from(provided, "utf8");
    if (
      expectedBytes.length === 0 ||
      expectedBytes.length !== providedBytes.length
    ) {
      return "external";
    }

    return timingSafeEqual(expectedBytes, providedBytes)
      ? "hosted_mcp"
      : "external";
  } catch {
    return "external";
  }
}

/**
 * Decorate a public REST handler with one post-response telemetry callback.
 * CDN cache hits and WAF-rejected requests do not execute this code and
 * therefore are not represented.
 */
export function withPublicApiObservability(
  route: PublicApiRestRoute,
  handler: RestHandler,
): RestHandler {
  return async function observedPublicApiRequest(request: NextRequest) {
    const startedAt = Date.now();
    const consumer = publicApiConsumerFor(request);
    let statusCode = 500;
    const searchMeasurement = route === "search" ? createSearchMeasurement() : null;

    try {
      const response = searchMeasurement
        ? await searchMeasurement.run(() => handler(request))
        : await handler(request);
      statusCode = response.status;
      return response;
    } finally {
      const durationMs = boundedDurationMs(startedAt);
      const rateLimited = statusCode === 429;
      const searchLatency = searchMeasurement?.finish();

      try {
        after(async () => {
          try {
            console.info("public_api.request", {
              route,
              interface: "rest",
              method: request.method === "POST" ? "POST" : "GET",
              consumer,
              status_class: statusClass(statusCode),
              rate_limited: rateLimited,
              duration_ms: durationMs,
            });
          } catch {
            // Telemetry is best-effort and must never change the API response.
          }

          if (searchLatency) {
            try {
              console.info("public_api.search_latency", JSON.stringify({
                ...searchLatency, consumer, status_class: statusClass(statusCode),
              }));
            } catch { /* Timing logs are best-effort, just like request metrics. */ }
          }

          try {
            let clientIp: string | null = null;
            try {
              clientIp = getClientIp(request.headers);
            } catch {
              // Keep the aggregate metric even if proxy headers are malformed.
            }
            await recordPublicApiMetric({
              interface: "rest",
              route,
              consumer,
              statusCode,
              durationMs,
              rateLimited,
              clientIp,
            });
          } catch {
            // Metrics are best-effort and must never change the API response.
          }
        });
      } catch {
        // `after()` registration itself is also non-critical telemetry work.
      }
    }
  };
}
