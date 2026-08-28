export const PUBLIC_API_METRIC_INTERFACES = ["rest", "mcp"] as const;
export type PublicApiMetricInterface =
  (typeof PUBLIC_API_METRIC_INTERFACES)[number];

export const PUBLIC_API_METRIC_ROUTES = [
  "search",
  "job",
  "companies",
  "taxonomies",
  "resolve",
  "watchlists",
  "watchlist_create",
  "mcp",
  "unknown",
] as const;
export type PublicApiMetricRoute = (typeof PUBLIC_API_METRIC_ROUTES)[number];

export const PUBLIC_API_METRIC_CONSUMERS = [
  "external",
  "hosted_mcp",
] as const;
export type PublicApiMetricConsumer =
  (typeof PUBLIC_API_METRIC_CONSUMERS)[number];

export const PUBLIC_API_METRIC_STATUS_CLASSES = [
  "1xx",
  "2xx",
  "3xx",
  "4xx",
  "5xx",
  "other",
] as const;
export type PublicApiMetricStatusClass =
  (typeof PUBLIC_API_METRIC_STATUS_CLASSES)[number];

export const PUBLIC_API_METRIC_LATENCY_BUCKETS = [
  "lt_50_ms",
  "50_199_ms",
  "200_499_ms",
  "500_999_ms",
  "gte_1000_ms",
] as const;
export type PublicApiMetricLatencyBucket =
  (typeof PUBLIC_API_METRIC_LATENCY_BUCKETS)[number];

export interface PublicApiMetricInput {
  interface: PublicApiMetricInterface;
  route: PublicApiMetricRoute;
  consumer: PublicApiMetricConsumer;
  statusCode: number;
  durationMs: number;
  rateLimited: boolean;
  clientIp: string | null;
  occurredAt?: Date;
}

export interface PublicApiCountDimensions {
  interface: PublicApiMetricInterface;
  route: PublicApiMetricRoute;
  consumer: PublicApiMetricConsumer;
  statusClass: PublicApiMetricStatusClass;
  latencyBucket: PublicApiMetricLatencyBucket;
  rateLimited: boolean;
  networkClientRecorded: boolean;
}

export const PUBLIC_API_METRICS_COUNTS_PREFIX =
  "metrics:public-api:v1:counts";
export const PUBLIC_API_METRICS_CLIENTS_PREFIX =
  "metrics:public-api:v1:clients";
export const PUBLIC_API_METRICS_RETENTION_DAYS = 90;

const MILLISECONDS_PER_DAY = 86_400_000;

export function publicApiMetricDay(occurredAt: Date): string {
  return occurredAt.toISOString().slice(0, 10);
}

export function publicApiCountsKey(day: string): string {
  return `${PUBLIC_API_METRICS_COUNTS_PREFIX}:${day}`;
}

export function publicApiClientsKey(
  day: string,
  metricInterface: PublicApiMetricInterface,
): string {
  return `${PUBLIC_API_METRICS_CLIENTS_PREFIX}:${day}:${metricInterface}:external`;
}

export function publicApiMetricExpiresAt(day: string): number {
  const start = Date.parse(`${day}T00:00:00.000Z`);
  if (!Number.isFinite(start)) {
    throw new Error("Invalid public API metric day");
  }
  return Math.floor(
    (start + PUBLIC_API_METRICS_RETENTION_DAYS * MILLISECONDS_PER_DAY) / 1000,
  );
}

export function publicApiMetricStatusClass(
  statusCode: number,
): PublicApiMetricStatusClass {
  if (!Number.isInteger(statusCode) || statusCode < 100 || statusCode > 599) {
    return "other";
  }
  return `${Math.floor(statusCode / 100)}xx` as PublicApiMetricStatusClass;
}

export function publicApiMetricLatencyBucket(
  durationMs: number,
): PublicApiMetricLatencyBucket {
  if (!Number.isFinite(durationMs) || durationMs < 0) return "gte_1000_ms";
  if (durationMs < 50) return "lt_50_ms";
  if (durationMs < 200) return "50_199_ms";
  if (durationMs < 500) return "200_499_ms";
  if (durationMs < 1_000) return "500_999_ms";
  return "gte_1000_ms";
}

export function encodePublicApiCountField(
  dimensions: PublicApiCountDimensions,
): string {
  return [
    `interface=${dimensions.interface}`,
    `route=${dimensions.route}`,
    `consumer=${dimensions.consumer}`,
    `status=${dimensions.statusClass}`,
    `latency=${dimensions.latencyBucket}`,
    `rate_limited=${dimensions.rateLimited ? "true" : "false"}`,
    `network_client=${dimensions.networkClientRecorded ? "true" : "false"}`,
  ].join("|");
}

const COUNT_FIELD_PATTERN =
  /^interface=([^|]+)\|route=([^|]+)\|consumer=([^|]+)\|status=([^|]+)\|latency=([^|]+)\|rate_limited=(true|false)\|network_client=(true|false)$/u;

export function parsePublicApiCountField(
  field: string,
): PublicApiCountDimensions | null {
  const match = field.match(COUNT_FIELD_PATTERN);
  if (!match) return null;

  const [, metricInterface, route, consumer, statusClass, latencyBucket] =
    match;
  if (
    !PUBLIC_API_METRIC_INTERFACES.includes(
      metricInterface as PublicApiMetricInterface,
    ) ||
    !PUBLIC_API_METRIC_ROUTES.includes(route as PublicApiMetricRoute) ||
    !PUBLIC_API_METRIC_CONSUMERS.includes(
      consumer as PublicApiMetricConsumer,
    ) ||
    !PUBLIC_API_METRIC_STATUS_CLASSES.includes(
      statusClass as PublicApiMetricStatusClass,
    ) ||
    !PUBLIC_API_METRIC_LATENCY_BUCKETS.includes(
      latencyBucket as PublicApiMetricLatencyBucket,
    )
  ) {
    return null;
  }

  return {
    interface: metricInterface as PublicApiMetricInterface,
    route: route as PublicApiMetricRoute,
    consumer: consumer as PublicApiMetricConsumer,
    statusClass: statusClass as PublicApiMetricStatusClass,
    latencyBucket: latencyBucket as PublicApiMetricLatencyBucket,
    rateLimited: match[6] === "true",
    networkClientRecorded: match[7] === "true",
  };
}
