import { createHmac } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

type PipelineCommand = { name: string; args: unknown[] };

const redisState = vi.hoisted(() => ({
  commands: [] as PipelineCommand[],
  execError: null as Error | null,
}));

vi.mock("@/lib/redis", () => ({
  redis: {
    pipeline: () => {
      const pending: PipelineCommand[] = [];
      const pipeline = {
        hincrby: (...args: unknown[]) => {
          pending.push({ name: "hincrby", args });
          return pipeline;
        },
        expireat: (...args: unknown[]) => {
          pending.push({ name: "expireat", args });
          return pipeline;
        },
        pfadd: (...args: unknown[]) => {
          pending.push({ name: "pfadd", args });
          return pipeline;
        },
        exec: async () => {
          if (redisState.execError) throw redisState.execError;
          redisState.commands.push(...pending);
          return pending.map(() => 1);
        },
      };
      return pipeline;
    },
  },
}));

import {
  parsePublicApiCountField,
  publicApiMetricExpiresAt,
} from "@/lib/public-api-metrics-contract";
import { recordPublicApiMetric } from "@/lib/public-api-metrics";

const BASE_INPUT = {
  interface: "rest" as const,
  route: "search" as const,
  consumer: "external" as const,
  statusCode: 200,
  durationMs: 125,
  rateLimited: false,
  clientIp: "203.0.113.7",
  occurredAt: new Date("2026-08-28T23:59:59.000Z"),
};

describe("recordPublicApiMetric", () => {
  withTestEnv({ API_METRICS_HMAC_SECRET: "test-only-metrics-secret" });

  beforeEach(() => {
    redisState.commands.length = 0;
    redisState.execError = null;
    vi.restoreAllMocks();
  });

  it("writes one bounded count field and one daily HLL in four billed commands", async () => {
    await expect(recordPublicApiMetric(BASE_INPUT)).resolves.toBeUndefined();

    expect(redisState.commands.map(({ name }) => name)).toEqual([
      "hincrby",
      "expireat",
      "pfadd",
      "expireat",
    ]);
    const [hincrby, countsExpiry, pfadd, clientsExpiry] = redisState.commands;
    expect(hincrby?.args[0]).toBe(
      "metrics:public-api:v1:counts:2026-08-28",
    );
    expect(parsePublicApiCountField(String(hincrby?.args[1]))).toEqual({
      interface: "rest",
      route: "search",
      consumer: "external",
      statusClass: "2xx",
      latencyBucket: "50_199_ms",
      rateLimited: false,
      networkClientRecorded: true,
    });
    expect(hincrby?.args[2]).toBe(1);
    const expiresAt = publicApiMetricExpiresAt("2026-08-28");
    expect(countsExpiry?.args).toEqual([
      "metrics:public-api:v1:counts:2026-08-28",
      expiresAt,
    ]);
    expect(pfadd?.args).toEqual([
      "metrics:public-api:v1:clients:2026-08-28:rest:external",
      createHmac("sha256", "test-only-metrics-secret")
        .update("2026-08-28")
        .update("\0")
        .update("203.0.113.7")
        .digest("hex"),
    ]);
    expect(clientsExpiry?.args).toEqual([
      "metrics:public-api:v1:clients:2026-08-28:rest:external",
      expiresAt,
    ]);

    const serialized = JSON.stringify(redisState.commands);
    expect(serialized).not.toContain("203.0.113.7");
    expect(serialized).not.toContain("test-only-metrics-secret");
  });

  it.each([
    [0, "lt_50_ms"],
    [49, "lt_50_ms"],
    [50, "50_199_ms"],
    [199, "50_199_ms"],
    [200, "200_499_ms"],
    [499, "200_499_ms"],
    [500, "500_999_ms"],
    [999, "500_999_ms"],
    [1_000, "gte_1000_ms"],
  ])("bounds %dms in %s", async (durationMs, expected) => {
    await recordPublicApiMetric({ ...BASE_INPUT, durationMs });
    const field = String(redisState.commands[0]?.args[1]);
    expect(parsePublicApiCountField(field)?.latencyBucket).toBe(expected);
  });

  it.each([
    [99, "other"],
    [100, "1xx"],
    [299, "2xx"],
    [399, "3xx"],
    [429, "4xx"],
    [599, "5xx"],
    [600, "other"],
  ])("bounds status %d in %s", async (statusCode, expected) => {
    await recordPublicApiMetric({ ...BASE_INPUT, statusCode });
    const field = String(redisState.commands[0]?.args[1]);
    expect(parsePublicApiCountField(field)?.statusClass).toBe(expected);
  });

  it("uses two commands and no HLL for hosted MCP downstream REST", async () => {
    await recordPublicApiMetric({
      ...BASE_INPUT,
      consumer: "hosted_mcp",
    });

    expect(redisState.commands.map(({ name }) => name)).toEqual([
      "hincrby",
      "expireat",
    ]);
    expect(
      parsePublicApiCountField(String(redisState.commands[0]?.args[1])),
    ).toMatchObject({
      consumer: "hosted_mcp",
      networkClientRecorded: false,
    });
  });

  it("skips HLL for unknown or invalid IP input", async () => {
    await recordPublicApiMetric({ ...BASE_INPUT, clientIp: "unknown" });
    expect(redisState.commands.map(({ name }) => name)).toEqual([
      "hincrby",
      "expireat",
    ]);
  });

  it("keeps the count but emits one sanitized event when the HMAC secret is absent", async () => {
    setTestEnv({ API_METRICS_HMAC_SECRET: undefined });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await expect(recordPublicApiMetric(BASE_INPUT)).resolves.toBeUndefined();

    expect(redisState.commands.map(({ name }) => name)).toEqual([
      "hincrby",
      "expireat",
    ]);
    expect(warn).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalledWith(
      JSON.stringify({
        event: "api_metrics.unavailable",
        reason: "missing_hmac_secret",
      }),
    );
  });

  it("always resolves and emits exactly one sanitized event on Redis failure", async () => {
    redisState.execError = new Error(
      "SECRET_CANARY_REDIS 203.0.113.7 test-only-metrics-secret",
    );
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await expect(recordPublicApiMetric(BASE_INPUT)).resolves.toBeUndefined();

    expect(warn).toHaveBeenCalledOnce();
    const serialized = JSON.stringify(warn.mock.calls);
    expect(serialized).toContain("api_metrics.unavailable");
    expect(serialized).not.toContain("SECRET_CANARY_REDIS");
    expect(serialized).not.toContain("203.0.113.7");
    expect(serialized).not.toContain("test-only-metrics-secret");
  });

  it("still resolves when both Redis and the unavailable-event logger fail", async () => {
    redisState.execError = new Error("Redis connection failed");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {
      throw new Error("Logger failed");
    });

    await expect(recordPublicApiMetric(BASE_INPUT)).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledOnce();
  });

  it("rejects unexpected route cardinality without writing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await recordPublicApiMetric({
      ...BASE_INPUT,
      route: "search?q=sensitive" as "search",
    });

    expect(redisState.commands).toEqual([]);
    expect(warn).toHaveBeenCalledWith(
      JSON.stringify({
        event: "api_metrics.unavailable",
        reason: "invalid_input",
      }),
    );
  });

  it("uses atomic increments for concurrent updates", async () => {
    await Promise.all(
      Array.from({ length: 20 }, () => recordPublicApiMetric(BASE_INPUT)),
    );

    expect(
      redisState.commands.filter(({ name }) => name === "hincrby"),
    ).toHaveLength(20);
    expect(
      redisState.commands
        .filter(({ name }) => name === "hincrby")
        .every(({ args }) => args[2] === 1),
    ).toBe(true);
  });
});
