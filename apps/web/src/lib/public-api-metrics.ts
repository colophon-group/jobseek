import "server-only";

import { createHmac } from "node:crypto";
import { isIP } from "node:net";
import { redis } from "@/lib/redis";
import {
  encodePublicApiCountField,
  PUBLIC_API_METRIC_ROUTES,
  publicApiClientsKey,
  publicApiCountsKey,
  publicApiMetricDay,
  publicApiMetricExpiresAt,
  publicApiMetricLatencyBucket,
  publicApiMetricStatusClass,
  type PublicApiMetricInput,
} from "@/lib/public-api-metrics-contract";

type UnavailableReason =
  | "invalid_input"
  | "missing_hmac_secret"
  | "redis_write_failed";

function emitUnavailable(reason: UnavailableReason): void {
  try {
    console.warn(
      JSON.stringify({
        event: "api_metrics.unavailable",
        reason,
      }),
    );
  } catch {
    // Even a broken logger must not make public API telemetry observable.
  }
}

function isValidInput(input: PublicApiMetricInput): boolean {
  return (
    (input.interface === "rest" || input.interface === "mcp") &&
    PUBLIC_API_METRIC_ROUTES.includes(input.route) &&
    (input.consumer === "external" || input.consumer === "hosted_mcp") &&
    input.occurredAt instanceof Date &&
    Number.isFinite(input.occurredAt.getTime())
  );
}

/**
 * Persist one bounded origin-execution metric without affecting the caller.
 *
 * A successful counts-only path issues two logical Redis commands (HINCRBY +
 * EXPIREAT). An external request with a valid platform-authoritative IP issues
 * four by adding PFADD + EXPIREAT for the daily network-client estimate.
 */
export async function recordPublicApiMetric(
  input: PublicApiMetricInput,
): Promise<void> {
  try {
    const occurredAt = input.occurredAt ?? new Date();
    const normalizedInput = { ...input, occurredAt };
    if (!isValidInput(normalizedInput)) {
      emitUnavailable("invalid_input");
      return;
    }

    const day = publicApiMetricDay(occurredAt);
    const expiresAt = publicApiMetricExpiresAt(day);
    const authoritativeExternalIp =
      input.consumer === "external" &&
      typeof input.clientIp === "string" &&
      isIP(input.clientIp) !== 0
        ? input.clientIp
        : null;
    const hmacSecret = process.env.API_METRICS_HMAC_SECRET;
    const networkClientRecorded = Boolean(
      authoritativeExternalIp && hmacSecret,
    );

    const field = encodePublicApiCountField({
      interface: input.interface,
      route: input.route,
      consumer: input.consumer,
      statusClass: publicApiMetricStatusClass(input.statusCode),
      latencyBucket: publicApiMetricLatencyBucket(input.durationMs),
      rateLimited: input.rateLimited,
      networkClientRecorded,
    });

    const countsKey = publicApiCountsKey(day);
    const pipeline = redis.pipeline();
    pipeline.hincrby(countsKey, field, 1);
    pipeline.expireat(countsKey, expiresAt);

    if (authoritativeExternalIp && hmacSecret) {
      const pseudonym = createHmac("sha256", hmacSecret)
        .update(day)
        .update("\0")
        .update(authoritativeExternalIp)
        .digest("hex");
      const clientsKey = publicApiClientsKey(day, input.interface);
      pipeline.pfadd(clientsKey, pseudonym);
      pipeline.expireat(clientsKey, expiresAt);
    }

    await pipeline.exec();

    if (authoritativeExternalIp && !hmacSecret) {
      emitUnavailable("missing_hmac_secret");
    }
  } catch {
    emitUnavailable("redis_write_failed");
  }
}
