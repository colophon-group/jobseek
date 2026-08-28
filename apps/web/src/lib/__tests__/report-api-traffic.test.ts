import { describe, expect, it } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";
import {
  buildApiTrafficReport,
  formatApiTrafficReport,
  parseApiTrafficArgs,
  resolveApiTrafficWindow,
  type ApiTrafficMetricSource,
  type RawApiTrafficDay,
} from "../../../script/report-api-traffic";
import {
  encodePublicApiCountField,
  type PublicApiCountDimensions,
} from "../public-api-metrics-contract";

function countField(
  overrides: Partial<PublicApiCountDimensions> = {},
): string {
  return encodePublicApiCountField({
    interface: "rest",
    route: "search",
    consumer: "external",
    statusClass: "2xx",
    latencyBucket: "50_199_ms",
    rateLimited: false,
    networkClientRecorded: true,
    ...overrides,
  });
}

function rawDay(
  counts: unknown = null,
  clients: RawApiTrafficDay["clients"] = {
    rest: { exists: 0, estimate: 0 },
    mcp: { exists: 0, estimate: 0 },
  },
): RawApiTrafficDay {
  return { counts, clients };
}

class FixtureSource implements ApiTrafficMetricSource {
  readonly reads: string[] = [];

  constructor(private readonly days: Record<string, RawApiTrafficDay>) {}

  async readDay(day: string): Promise<RawApiTrafficDay> {
    this.reads.push(day);
    return this.days[day] ?? rawDay();
  }
}

describe("API traffic CLI arguments and UTC window", () => {
  it("parses the supported stable CLI surface", () => {
    expect(
      parseApiTrafficArgs([
        "--since",
        "30d",
        "--through",
        "2026-08-20",
        "--json",
        "--env-file",
        "/tmp/test.env",
      ]),
    ).toEqual({
      since: "30d",
      through: "2026-08-20",
      json: true,
      envFile: "/tmp/test.env",
      help: false,
    });
  });

  it.each([
    [[], "--since is required"],
    [["--since", "1d"], "--since must be one of"],
    [["--since", "7d", "--through", "2026-02-30"], "valid UTC calendar date"],
    [["--since", "7d", "--wat"], "Unknown argument"],
  ])("rejects invalid arguments", (args, message) => {
    expect(() => parseApiTrafficArgs(args)).toThrow(message);
  });

  it("uses the last completed UTC day by default", () => {
    expect(
      resolveApiTrafficWindow(
        { since: "7d" },
        new Date("2026-08-28T00:01:00.000Z"),
      ),
    ).toEqual({
      dates: [
        "2026-08-21",
        "2026-08-22",
        "2026-08-23",
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
      ],
      start: "2026-08-21",
      through: "2026-08-27",
      includesPartialDay: false,
    });
  });

  it("marks an explicitly included current UTC day as partial", () => {
    const window = resolveApiTrafficWindow(
      { since: "7d", through: "2026-08-28" },
      new Date("2026-08-28T23:59:00.000Z"),
    );
    expect(window.includesPartialDay).toBe(true);
    expect(window.dates.at(-1)).toBe("2026-08-28");
  });
});

describe("buildApiTrafficReport", () => {
  withTestEnv({
    UPSTASH_REDIS_REST_URL: undefined,
    UPSTASH_REDIS_REST_TOKEN: undefined,
  });

  it("aggregates daily bounded dimensions, HLL estimates, coverage, and exact write commands", async () => {
    const source = new FixtureSource({
      "2026-08-27": rawDay(
        {
          [countField()]: "3",
          [countField({
            consumer: "hosted_mcp",
            route: "job",
            latencyBucket: "200_499_ms",
            networkClientRecorded: false,
          })]: 2,
          [countField({
            interface: "mcp",
            route: "mcp",
            statusClass: "4xx",
            latencyBucket: "gte_1000_ms",
            rateLimited: true,
          })]: 4,
        },
        {
          rest: { exists: 1, estimate: 2 },
          mcp: { exists: "1", estimate: "3" },
        },
      ),
    });

    const report = await buildApiTrafficReport(
      source,
      { since: "7d" },
      new Date("2026-08-28T12:00:00.000Z"),
    );

    expect(source.reads).toEqual([
      "2026-08-21",
      "2026-08-22",
      "2026-08-23",
      "2026-08-24",
      "2026-08-25",
      "2026-08-26",
      "2026-08-27",
    ]);
    expect(report.schema_version).toBe(1);
    expect(report.total).toBe(9);
    expect(report.interfaces).toEqual({ rest: 5, mcp: 4 });
    expect(report.rest_consumers).toEqual({ external: 3, hosted_mcp: 2 });
    expect(report.routes).toMatchObject({ search: 3, job: 2, mcp: 4 });
    expect(report.status_classes).toMatchObject({ "2xx": 5, "4xx": 4 });
    expect(report.rate_limited).toEqual({ true: 4, false: 5 });
    expect(report.network_client_days).toEqual({
      rest_external: 2,
      mcp_external: 3,
      observed_days: { rest_external: 1, mcp_external: 1 },
    });
    expect(report.telemetry_write_failures).toBeNull();
    expect(report.coverage).toEqual({
      present_days: 1,
      missing_days: [
        "2026-08-21",
        "2026-08-22",
        "2026-08-23",
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
      ],
      corrupt_days: [],
      partial_days: [],
      first_day_present: "2026-08-27",
      last_day_present: "2026-08-27",
    });
    // 9 requests * 2 count commands + 7 HLL-eligible requests * 2 commands.
    expect(report.budget).toMatchObject({
      accounting_basis: "logical_commands_implied_by_retained_counters",
      logical_metric_write_commands: 32,
      requests_with_network_client_hll: 7,
      logical_commands_per_successful_write: {
        counts_only: 2,
        with_network_client_hll: 4,
      },
      redis_plan_command_limit: null,
      redis_plan_command_utilization: null,
      storage_bytes: null,
    });
    expect(report.semantics.write_command_accounting).toBe(
      "retained_counter_dimensions_not_provider_billing",
    );
    expect(report.daily.at(-1)?.logical_metric_write_commands).toBe(32);
  });

  it("surfaces corrupt, missing-HLL, orphan-HLL, and partial coverage without leaking credentials", async () => {
    setTestEnv({
      UPSTASH_REDIS_REST_URL: "SECRET_CANARY_URL",
      UPSTASH_REDIS_REST_TOKEN: "SECRET_CANARY_TOKEN",
    });
    const source = new FixtureSource({
      "2026-08-27": rawDay(
        {
          [countField()]: 2,
          "route=search?q=private": 7,
        },
        {
          rest: { exists: 0, estimate: 0 },
          mcp: { exists: 0, estimate: 0 },
        },
      ),
      "2026-08-28": rawDay(null, {
        rest: { exists: 1, estimate: 1 },
        mcp: { exists: 0, estimate: 0 },
      }),
    });

    const report = await buildApiTrafficReport(
      source,
      { since: "7d", through: "2026-08-28" },
      new Date("2026-08-28T15:00:00.000Z"),
    );
    const corrupt = report.daily.find(({ date }) => date === "2026-08-27")!;
    const partial = report.daily.find(({ date }) => date === "2026-08-28")!;

    expect(corrupt.coverage).toBe("corrupt");
    expect(corrupt.total).toBe(2);
    expect(corrupt.network_clients.rest_external).toBeNull();
    expect(partial.coverage).toBe("missing");
    expect(partial.partial).toBe(true);
    expect(partial.network_clients.rest_external).toBeNull();
    expect(report.coverage.corrupt_days).toEqual(["2026-08-27"]);
    expect(report.coverage.partial_days).toEqual(["2026-08-28"]);
    expect(report.warnings.join("\n")).toContain("orphaned REST network-client HLL");
    expect(JSON.stringify(report)).not.toContain("SECRET_CANARY");
  });

  it("labels daily sums as network-client-days rather than cross-day uniques", async () => {
    const counts = { [countField()]: 5 };
    const source = new FixtureSource({
      "2026-08-26": rawDay(counts, {
        rest: { exists: 1, estimate: 3 },
        mcp: { exists: 0, estimate: 0 },
      }),
      "2026-08-27": rawDay(counts, {
        rest: { exists: 1, estimate: 3 },
        mcp: { exists: 0, estimate: 0 },
      }),
    });

    const report = await buildApiTrafficReport(
      source,
      { since: "7d" },
      new Date("2026-08-28T12:00:00.000Z"),
    );

    expect(report.network_client_days.rest_external).toBe(6);
    expect(report.network_client_days.observed_days.rest_external).toBe(2);
    expect(report.semantics.period_network_clients).toBe(
      "network_client_days_not_cross_day_uniques",
    );
    expect(formatApiTrafficReport(report)).toContain(
      "daily HLL estimates, not cross-day uniques",
    );
    expect(formatApiTrafficReport(report)).toContain("not provider billing");
  });
});
