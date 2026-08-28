import { Redis } from "@upstash/redis";
import { config as loadDotEnv } from "dotenv";
import { pathToFileURL } from "node:url";
import {
  parsePublicApiCountField,
  publicApiClientsKey,
  publicApiCountsKey,
  PUBLIC_API_METRIC_CONSUMERS,
  PUBLIC_API_METRIC_INTERFACES,
  PUBLIC_API_METRIC_LATENCY_BUCKETS,
  PUBLIC_API_METRIC_ROUTES,
  PUBLIC_API_METRIC_STATUS_CLASSES,
  type PublicApiMetricConsumer,
  type PublicApiMetricInterface,
  type PublicApiMetricLatencyBucket,
  type PublicApiMetricRoute,
  type PublicApiMetricStatusClass,
} from "../src/lib/public-api-metrics-contract";

const DAY_MS = 86_400_000;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/u;
const SINCE_DAYS = { "7d": 7, "30d": 30, "90d": 90 } as const;

export type ApiTrafficSince = keyof typeof SINCE_DAYS;

export interface ApiTrafficCliOptions {
  since: ApiTrafficSince;
  through?: string;
  json: boolean;
  envFile?: string;
  help: boolean;
}

export interface RawApiTrafficDay {
  counts: unknown;
  clients: Record<
    PublicApiMetricInterface,
    { exists: unknown; estimate: unknown }
  >;
}

export interface ApiTrafficMetricSource {
  readDay(day: string): Promise<RawApiTrafficDay>;
}

type NumericDimensions = {
  interfaces: Record<PublicApiMetricInterface, number>;
  routes: Record<PublicApiMetricRoute, number>;
  consumers: Record<PublicApiMetricConsumer, number>;
  rest_consumers: Record<PublicApiMetricConsumer, number>;
  status_classes: Record<PublicApiMetricStatusClass, number>;
  latency_buckets: Record<PublicApiMetricLatencyBucket, number>;
  rate_limited: { true: number; false: number };
};

export interface DailyApiTrafficReport extends NumericDimensions {
  date: string;
  coverage: "present" | "missing" | "corrupt";
  partial: boolean;
  total: number;
  network_clients: {
    rest_external: number | null;
    mcp_external: number | null;
  };
  logical_metric_write_commands: number;
  warnings: string[];
}

export interface ApiTrafficReport extends NumericDimensions {
  schema_version: 1;
  generated_at: string;
  window: {
    since: ApiTrafficSince;
    days: number;
    start: string;
    through: string;
    timezone: "UTC";
    includes_partial_day: boolean;
  };
  semantics: {
    traffic_scope: "origin_executions_only";
    network_clients: "daily_hll_estimate_of_network_clients_not_people";
    period_network_clients: "network_client_days_not_cross_day_uniques";
    write_command_accounting: "retained_counter_dimensions_not_provider_billing";
  };
  total: number;
  network_client_days: {
    rest_external: number;
    mcp_external: number;
    observed_days: {
      rest_external: number;
      mcp_external: number;
    };
  };
  telemetry_write_failures: null;
  coverage: {
    present_days: number;
    missing_days: string[];
    corrupt_days: string[];
    partial_days: string[];
    first_day_present: string | null;
    last_day_present: string | null;
  };
  budget: {
    accounting_basis: "logical_commands_implied_by_retained_counters";
    logical_commands_per_successful_write: {
      counts_only: 2;
      with_network_client_hll: 4;
    };
    logical_metric_write_commands: number;
    requests_with_network_client_hll: number;
    redis_plan_command_limit: null;
    redis_plan_command_utilization: null;
    storage_bytes: null;
  };
  daily: DailyApiTrafficReport[];
  warnings: string[];
}

function emptyRecord<T extends readonly string[]>(
  values: T,
): Record<T[number], number> {
  return Object.fromEntries(values.map((value) => [value, 0])) as Record<
    T[number],
    number
  >;
}

function emptyDimensions(): NumericDimensions {
  return {
    interfaces: emptyRecord(PUBLIC_API_METRIC_INTERFACES),
    routes: emptyRecord(PUBLIC_API_METRIC_ROUTES),
    consumers: emptyRecord(PUBLIC_API_METRIC_CONSUMERS),
    rest_consumers: emptyRecord(PUBLIC_API_METRIC_CONSUMERS),
    status_classes: emptyRecord(PUBLIC_API_METRIC_STATUS_CLASSES),
    latency_buckets: emptyRecord(PUBLIC_API_METRIC_LATENCY_BUCKETS),
    rate_limited: { true: 0, false: 0 },
  };
}

function parseDate(value: string, label: string): number {
  if (!DATE_PATTERN.test(value)) {
    throw new Error(`${label} must be YYYY-MM-DD`);
  }
  const timestamp = Date.parse(`${value}T00:00:00.000Z`);
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) {
    throw new Error(`${label} must be a valid UTC calendar date`);
  }
  return timestamp;
}

function utcDay(timestamp: number): string {
  return new Date(timestamp).toISOString().slice(0, 10);
}

export function parseApiTrafficArgs(args: string[]): ApiTrafficCliOptions {
  let since: ApiTrafficSince | undefined;
  let through: string | undefined;
  let envFile: string | undefined;
  let json = false;
  let help = false;

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--") continue;
    if (argument === "--help" || argument === "-h") {
      help = true;
      continue;
    }
    if (argument === "--json") {
      json = true;
      continue;
    }
    if (argument === "--since") {
      const value = args[index + 1];
      if (!value || !(value in SINCE_DAYS)) {
        throw new Error("--since must be one of 7d, 30d, or 90d");
      }
      since = value as ApiTrafficSince;
      index += 1;
      continue;
    }
    if (argument === "--through") {
      const value = args[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error("--through requires YYYY-MM-DD");
      }
      parseDate(value, "--through");
      through = value;
      index += 1;
      continue;
    }
    if (argument === "--env-file") {
      const value = args[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error("--env-file requires a path");
      }
      envFile = value;
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${argument}`);
  }

  if (!help && !since) {
    throw new Error("--since is required (7d, 30d, or 90d)");
  }
  return { since: since ?? "7d", through, json, envFile, help };
}

export function resolveApiTrafficWindow(
  options: Pick<ApiTrafficCliOptions, "since" | "through">,
  now = new Date(),
): {
  dates: string[];
  start: string;
  through: string;
  includesPartialDay: boolean;
} {
  const today = utcDay(now.getTime());
  const todayTimestamp = parseDate(today, "current UTC day");
  const throughTimestamp = options.through
    ? parseDate(options.through, "--through")
    : todayTimestamp - DAY_MS;
  if (throughTimestamp > todayTimestamp) {
    throw new Error("--through cannot be in the future");
  }

  const days = SINCE_DAYS[options.since];
  const startTimestamp = throughTimestamp - (days - 1) * DAY_MS;
  const dates = Array.from({ length: days }, (_, index) =>
    utcDay(startTimestamp + index * DAY_MS),
  );
  return {
    dates,
    start: dates[0]!,
    through: dates.at(-1)!,
    includesPartialDay: throughTimestamp === todayTimestamp,
  };
}

function asNonNegativeInteger(value: unknown): number | null {
  const number =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : Number.NaN;
  return Number.isSafeInteger(number) && number >= 0 ? number : null;
}

function asExists(value: unknown): boolean | null {
  const integer = asNonNegativeInteger(value);
  if (integer === null || integer > 1) return null;
  return integer === 1;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function clientEstimate(
  day: string,
  metricInterface: PublicApiMetricInterface,
  externalRequests: number,
  raw: { exists: unknown; estimate: unknown },
  warnings: string[],
): number | null {
  const exists = asExists(raw.exists);
  const estimate = asNonNegativeInteger(raw.estimate);
  if (exists === null || estimate === null) {
    warnings.push(`${day}: corrupt ${metricInterface} network-client estimate`);
    return null;
  }
  if (exists) return estimate;
  if (externalRequests === 0) return 0;
  warnings.push(
    `${day}: ${metricInterface} network-client estimate missing despite external origin traffic`,
  );
  return null;
}

function parseDailyReport(
  day: string,
  raw: RawApiTrafficDay,
  partial: boolean,
): DailyApiTrafficReport {
  const dimensions = emptyDimensions();
  const warnings: string[] = [];
  let total = 0;
  let requestsWithNetworkClientHll = 0;
  let corrupt = false;
  const counts = raw.counts;
  const entries = isRecord(counts) ? Object.entries(counts) : [];

  if (counts !== null && counts !== undefined && !isRecord(counts)) {
    corrupt = true;
    warnings.push(`${day}: counts hash is not an object`);
  }

  for (const [field, rawCount] of entries) {
    const parsed = parsePublicApiCountField(field);
    const count = asNonNegativeInteger(rawCount);
    if (!parsed || count === null || count === 0) {
      corrupt = true;
      warnings.push(`${day}: ignored corrupt count field`);
      continue;
    }
    total += count;
    dimensions.interfaces[parsed.interface] += count;
    dimensions.routes[parsed.route] += count;
    dimensions.consumers[parsed.consumer] += count;
    if (parsed.interface === "rest") {
      dimensions.rest_consumers[parsed.consumer] += count;
    }
    dimensions.status_classes[parsed.statusClass] += count;
    dimensions.latency_buckets[parsed.latencyBucket] += count;
    dimensions.rate_limited[parsed.rateLimited ? "true" : "false"] += count;
    if (parsed.networkClientRecorded) requestsWithNetworkClientHll += count;
  }

  const coverage = corrupt
    ? "corrupt"
    : entries.length === 0
      ? "missing"
      : "present";
  if (coverage === "missing") warnings.push(`${day}: counts missing`);
  if (partial) warnings.push(`${day}: current UTC day is partial`);

  const restExternal = entries.length === 0
    ? 0
    : dimensions.interfaces.rest > 0
      ? entries.reduce((sum, [field, rawCount]) => {
          const parsed = parsePublicApiCountField(field);
          const count = asNonNegativeInteger(rawCount);
          return parsed?.interface === "rest" && parsed.consumer === "external" && count
            ? sum + count
            : sum;
        }, 0)
      : 0;
  const mcpExternal = entries.length === 0
    ? 0
    : entries.reduce((sum, [field, rawCount]) => {
        const parsed = parsePublicApiCountField(field);
        const count = asNonNegativeInteger(rawCount);
        return parsed?.interface === "mcp" && parsed.consumer === "external" && count
          ? sum + count
          : sum;
      }, 0);

  const restEstimate = coverage === "missing"
    ? null
    : clientEstimate(
        day,
        "rest",
        restExternal,
        raw.clients.rest,
        warnings,
      );
  const mcpEstimate = coverage === "missing"
    ? null
    : clientEstimate(
        day,
        "mcp",
        mcpExternal,
        raw.clients.mcp,
        warnings,
      );
  if (coverage === "missing") {
    if (asExists(raw.clients.rest.exists) === true) {
      warnings.push(`${day}: orphaned REST network-client HLL without counts`);
    }
    if (asExists(raw.clients.mcp.exists) === true) {
      warnings.push(`${day}: orphaned MCP network-client HLL without counts`);
    }
  }

  return {
    date: day,
    coverage,
    partial,
    total,
    ...dimensions,
    network_clients: {
      rest_external: restEstimate,
      mcp_external: mcpEstimate,
    },
    logical_metric_write_commands:
      total * 2 + requestsWithNetworkClientHll * 2,
    warnings,
  };
}

function addDimensions(target: NumericDimensions, source: NumericDimensions): void {
  for (const key of PUBLIC_API_METRIC_INTERFACES) {
    target.interfaces[key] += source.interfaces[key];
  }
  for (const key of PUBLIC_API_METRIC_ROUTES) {
    target.routes[key] += source.routes[key];
  }
  for (const key of PUBLIC_API_METRIC_CONSUMERS) {
    target.consumers[key] += source.consumers[key];
    target.rest_consumers[key] += source.rest_consumers[key];
  }
  for (const key of PUBLIC_API_METRIC_STATUS_CLASSES) {
    target.status_classes[key] += source.status_classes[key];
  }
  for (const key of PUBLIC_API_METRIC_LATENCY_BUCKETS) {
    target.latency_buckets[key] += source.latency_buckets[key];
  }
  target.rate_limited.true += source.rate_limited.true;
  target.rate_limited.false += source.rate_limited.false;
}

export async function buildApiTrafficReport(
  source: ApiTrafficMetricSource,
  options: Pick<ApiTrafficCliOptions, "since" | "through">,
  now = new Date(),
): Promise<ApiTrafficReport> {
  const window = resolveApiTrafficWindow(options, now);
  const currentDay = utcDay(now.getTime());
  const daily: DailyApiTrafficReport[] = [];
  for (const day of window.dates) {
    daily.push(
      parseDailyReport(day, await source.readDay(day), day === currentDay),
    );
  }
  const totals = emptyDimensions();
  for (const day of daily) addDimensions(totals, day);

  const presentDates = daily
    .filter(({ coverage }) => coverage !== "missing")
    .map(({ date }) => date);
  const missingDays = daily
    .filter(({ coverage }) => coverage === "missing")
    .map(({ date }) => date);
  const corruptDays = daily
    .filter(({ coverage }) => coverage === "corrupt")
    .map(({ date }) => date);
  const partialDays = daily.filter(({ partial }) => partial).map(({ date }) => date);
  const restClientDays = daily.flatMap(({ network_clients }) =>
    network_clients.rest_external === null ? [] : [network_clients.rest_external],
  );
  const mcpClientDays = daily.flatMap(({ network_clients }) =>
    network_clients.mcp_external === null ? [] : [network_clients.mcp_external],
  );
  const logicalMetricWriteCommands = daily.reduce(
    (sum, day) => sum + day.logical_metric_write_commands,
    0,
  );
  const requestsWithNetworkClientHll = daily.reduce(
    (sum, day) =>
      sum + (day.logical_metric_write_commands - day.total * 2) / 2,
    0,
  );
  const warnings = [
    ...daily.flatMap(({ warnings: dayWarnings }) => dayWarnings),
    "telemetry write failures are unavailable because a failed Redis write cannot self-report",
    "origin counters exclude requests served entirely by Vercel CDN and requests stopped by WAF",
    "logical Redis command counts are inferred from retained counter dimensions; failed or partially executed pipelines and provider plan usage are not measurable from these keys",
  ];

  return {
    schema_version: 1,
    generated_at: now.toISOString(),
    window: {
      since: options.since,
      days: SINCE_DAYS[options.since],
      start: window.start,
      through: window.through,
      timezone: "UTC",
      includes_partial_day: window.includesPartialDay,
    },
    semantics: {
      traffic_scope: "origin_executions_only",
      network_clients: "daily_hll_estimate_of_network_clients_not_people",
      period_network_clients: "network_client_days_not_cross_day_uniques",
      write_command_accounting:
        "retained_counter_dimensions_not_provider_billing",
    },
    total: daily.reduce((sum, day) => sum + day.total, 0),
    ...totals,
    network_client_days: {
      rest_external: restClientDays.reduce((sum, count) => sum + count, 0),
      mcp_external: mcpClientDays.reduce((sum, count) => sum + count, 0),
      observed_days: {
        rest_external: restClientDays.length,
        mcp_external: mcpClientDays.length,
      },
    },
    telemetry_write_failures: null,
    coverage: {
      present_days: presentDates.length,
      missing_days: missingDays,
      corrupt_days: corruptDays,
      partial_days: partialDays,
      first_day_present: presentDates[0] ?? null,
      last_day_present: presentDates.at(-1) ?? null,
    },
    budget: {
      accounting_basis: "logical_commands_implied_by_retained_counters",
      logical_commands_per_successful_write: {
        counts_only: 2,
        with_network_client_hll: 4,
      },
      logical_metric_write_commands: logicalMetricWriteCommands,
      requests_with_network_client_hll: requestsWithNetworkClientHll,
      redis_plan_command_limit: null,
      redis_plan_command_utilization: null,
      storage_bytes: null,
    },
    daily,
    warnings,
  };
}

export function createUpstashApiTrafficSource(redis: Redis): ApiTrafficMetricSource {
  return {
    async readDay(day) {
      const pipeline = redis.pipeline();
      pipeline.hgetall(publicApiCountsKey(day));
      for (const metricInterface of PUBLIC_API_METRIC_INTERFACES) {
        const key = publicApiClientsKey(day, metricInterface);
        pipeline.exists(key);
        pipeline.pfcount(key);
      }
      const results = (await pipeline.exec()) as unknown[];
      return {
        counts: results[0] ?? null,
        clients: {
          rest: { exists: results[1], estimate: results[2] },
          mcp: { exists: results[3], estimate: results[4] },
        },
      };
    },
  };
}

function percent(numerator: number, denominator: number): string {
  return denominator === 0 ? "0.0%" : `${((numerator / denominator) * 100).toFixed(1)}%`;
}

function compactNonZero(values: Record<string, number>): string {
  const entries = Object.entries(values).filter(([, count]) => count > 0);
  return entries.length === 0
    ? "none"
    : entries.map(([value, count]) => `${value}=${count}`).join(", ");
}

export function formatApiTrafficReport(report: ApiTrafficReport): string {
  const lines = [
    `Public API origin traffic: ${report.window.start} through ${report.window.through} (${report.window.timezone})`,
    `Coverage: ${report.coverage.present_days}/${report.window.days} days present; missing=${report.coverage.missing_days.length}; corrupt=${report.coverage.corrupt_days.length}; partial=${report.coverage.partial_days.length}`,
    `Origin executions: ${report.total} (REST ${report.interfaces.rest}, MCP ${report.interfaces.mcp})`,
    `REST consumers: external=${report.rest_consumers.external}, hosted_mcp=${report.rest_consumers.hosted_mcp}`,
    `Rate limited: ${report.rate_limited.true} (${percent(report.rate_limited.true, report.total)})`,
    `Status classes: ${compactNonZero(report.status_classes)}`,
    `Routes: ${compactNonZero(report.routes)}`,
    `Latency buckets: ${compactNonZero(report.latency_buckets)}`,
    `Network-client-days (daily HLL estimates, not cross-day uniques): REST external=${report.network_client_days.rest_external}; MCP external=${report.network_client_days.mcp_external}`,
    `Logical metric Redis commands represented by retained counters: ${report.budget.logical_metric_write_commands} (${report.budget.logical_commands_per_successful_write.counts_only} per successful counts-only write; ${report.budget.logical_commands_per_successful_write.with_network_client_hll} with HLL; not provider billing)`,
    "",
    "Daily:",
    "date        coverage total rest mcp rate_limited rest_clients mcp_clients",
    ...report.daily.map(
      (day) =>
        `${day.date}  ${day.coverage.padEnd(8)} ${String(day.total).padStart(5)} ${String(day.interfaces.rest).padStart(4)} ${String(day.interfaces.mcp).padStart(3)} ${String(day.rate_limited.true).padStart(12)} ${String(day.network_clients.rest_external ?? "n/a").padStart(12)} ${String(day.network_clients.mcp_external ?? "n/a").padStart(11)}`,
    ),
  ];
  if (report.warnings.length > 0) {
    lines.push("", "Warnings:", ...report.warnings.map((warning) => `- ${warning}`));
  }
  return `${lines.join("\n")}\n`;
}

function usage(): string {
  return [
    "Usage: report-api-traffic --since 7d|30d|90d [--through YYYY-MM-DD] [--json] [--env-file PATH]",
    "",
    "The default --through date is the last completed UTC day.",
  ].join("\n");
}

async function main(): Promise<void> {
  const options = parseApiTrafficArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (options.envFile) {
    const result = loadDotEnv({ path: options.envFile, override: false, quiet: true });
    if (result.error) throw new Error(`Could not load --env-file: ${result.error.message}`);
  }
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    throw new Error(
      "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required (use --env-file PATH)",
    );
  }

  const redis = new Redis({ url, token });
  const report = await buildApiTrafficReport(
    createUpstashApiTrafficSource(redis),
    options,
  );
  process.stdout.write(
    options.json
      ? `${JSON.stringify(report, null, 2)}\n`
      : formatApiTrafficReport(report),
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "Unknown error";
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
  });
}
