import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  afterCallbacks: [] as Array<() => Promise<void> | void>,
  getClientIp: vi.fn(),
  recordPublicApiMetric: vi.fn(),
}));

vi.mock("next/server", () => ({
  after: vi.fn((callback: () => Promise<void> | void) => {
    mocks.afterCallbacks.push(callback);
  }),
}));

vi.mock("@/lib/rate-limit", () => ({
  getClientIp: mocks.getClientIp,
}));

vi.mock("@/lib/public-api-metrics", () => ({
  recordPublicApiMetric: mocks.recordPublicApiMetric,
}));

import { withPublicApiObservability } from "../public-api-observability";

function request(headers?: HeadersInit, query = ""): NextRequest {
  return new Request(`https://example.com/api/v1/search${query}`, {
    headers,
  }) as NextRequest;
}

async function drainAfter(): Promise<void> {
  const callbacks = mocks.afterCallbacks.splice(0);
  for (const callback of callbacks) await callback();
}

describe("public REST API observability", () => {
  withTestEnv({
    HOSTED_MCP_API_PROVENANCE_TOKEN: "valid-private-token",
  });

  beforeEach(() => {
    mocks.afterCallbacks.length = 0;
    mocks.getClientIp.mockReset();
    mocks.getClientIp.mockReturnValue("203.0.113.9");
    mocks.recordPublicApiMetric.mockReset();
    mocks.recordPublicApiMetric.mockResolvedValue(undefined);
    vi.spyOn(console, "info").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["missing", undefined, undefined, "external"],
    ["valid", "valid-private-token", undefined, "hosted_mcp"],
    ["malformed", "short", undefined, "external"],
    ["mismatch", "xalid-private-token", undefined, "external"],
    ["spoofed label", undefined, "hosted_mcp", "external"],
  ])(
    "classifies %s provenance as %s",
    async (_caseName, token, publicLabel, expectedConsumer) => {
      const headers = new Headers();
      if (token) headers.set("x-jobseek-internal-mcp-token", token);
      if (publicLabel) headers.set("x-jobseek-consumer", publicLabel);
      const observed = withPublicApiObservability(
        "search",
        async () => new Response(null, { status: 200 }),
      );

      await observed(request(headers));
      await drainAfter();

      expect(mocks.recordPublicApiMetric).toHaveBeenCalledWith(
        expect.objectContaining({ consumer: expectedConsumer }),
      );
    },
  );

  it("treats a supplied token as external when the server secret is absent", async () => {
    setTestEnv({ HOSTED_MCP_API_PROVENANCE_TOKEN: undefined });
    const observed = withPublicApiObservability(
      "job",
      async () => new Response(null, { status: 200 }),
    );

    await observed(
      request({ "x-jobseek-internal-mcp-token": "valid-private-token" }),
    );
    await drainAfter();

    expect(mocks.recordPublicApiMetric).toHaveBeenCalledWith(
      expect.objectContaining({ consumer: "external" }),
    );
  });

  it.each([
    [200, "2xx", false],
    [400, "4xx", false],
    [429, "4xx", true],
    [500, "5xx", false],
  ])(
    "emits exactly once for status %i",
    async (statusCode, expectedClass, rateLimited) => {
      const observed = withPublicApiObservability(
        "companies",
        async () => new Response(null, { status: statusCode }),
      );

      const response = await observed(request());
      expect(response.status).toBe(statusCode);
      expect(mocks.afterCallbacks).toHaveLength(1);
      await drainAfter();

      expect(console.info).toHaveBeenCalledTimes(1);
      expect(console.info).toHaveBeenCalledWith("public_api.request", {
        route: "companies",
        interface: "rest",
        method: "GET",
        consumer: "external",
        status_class: expectedClass,
        rate_limited: rateLimited,
        duration_ms: expect.any(Number),
      });
      expect(mocks.recordPublicApiMetric).toHaveBeenCalledTimes(1);
      expect(mocks.recordPublicApiMetric).toHaveBeenCalledWith({
        interface: "rest",
        route: "companies",
        consumer: "external",
        statusCode,
        durationMs: expect.any(Number),
        rateLimited,
        clientIp: "203.0.113.9",
      });
    },
  );

  it("emits one fixed 5xx event when the handler throws, then preserves the error", async () => {
    const thrown = new Error("SECRET_EXTERNAL_ERROR_CANARY");
    const observed = withPublicApiObservability("taxonomies", async () => {
      throw thrown;
    });

    await expect(
      observed(request(undefined, "?q=SECRET_QUERY_CANARY")),
    ).rejects.toBe(thrown);
    expect(mocks.afterCallbacks).toHaveLength(1);
    await drainAfter();

    expect(console.info).toHaveBeenCalledTimes(1);
    expect(mocks.recordPublicApiMetric).toHaveBeenCalledTimes(1);
    expect(mocks.recordPublicApiMetric).toHaveBeenCalledWith(
      expect.objectContaining({ statusCode: 500, route: "taxonomies" }),
    );
    expect(JSON.stringify(vi.mocked(console.info).mock.calls)).not.toContain(
      "SECRET_EXTERNAL_ERROR_CANARY",
    );
    expect(JSON.stringify(vi.mocked(console.info).mock.calls)).not.toContain(
      "SECRET_QUERY_CANARY",
    );
  });

  it("never serializes the provenance token or raw client IP", async () => {
    const observed = withPublicApiObservability(
      "resolve",
      async () => new Response(null, { status: 200 }),
    );

    await observed(
      request(
        {
          "x-jobseek-internal-mcp-token": "valid-private-token",
          "x-forwarded-for": "203.0.113.9",
        },
        "?q=SECRET_QUERY_CANARY",
      ),
    );
    await drainAfter();

    const serializedLogs = JSON.stringify(vi.mocked(console.info).mock.calls);
    expect(serializedLogs).not.toContain("valid-private-token");
    expect(serializedLogs).not.toContain("203.0.113.9");
    expect(serializedLogs).not.toContain("SECRET_QUERY_CANARY");
  });

  it("isolates console and metric failures from the response", async () => {
    vi.mocked(console.info).mockImplementation(() => {
      throw new Error("log unavailable");
    });
    mocks.recordPublicApiMetric.mockRejectedValueOnce(
      new Error("metrics unavailable"),
    );
    const observed = withPublicApiObservability(
      "watchlists",
      async () => new Response("preserved", { status: 200 }),
    );

    const response = await observed(request());
    await expect(drainAfter()).resolves.toBeUndefined();

    expect(await response.text()).toBe("preserved");
    expect(mocks.recordPublicApiMetric).toHaveBeenCalledTimes(1);
  });
});
