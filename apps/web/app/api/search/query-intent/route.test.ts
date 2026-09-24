import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const burst = vi.fn();
const sustained = vi.fn();
const propose = vi.fn();
vi.mock("@/lib/rate-limit", () => ({
  getClientIp: () => "test-ip",
  queryIntentBurstLimiter: { limit: (...args: unknown[]) => burst(...args) },
  queryIntentSustainedLimiter: { limit: (...args: unknown[]) => sustained(...args) },
}));
vi.mock("@/lib/services/query-intent", () => ({
  QueryIntentError: class QueryIntentError extends Error {},
  proposeQueryFilters: (...args: unknown[]) => propose(...args),
}));
vi.mock("@/lib/safe-external-error", () => ({ logExternalError: vi.fn() }));

import { POST } from "./route";

function request(query = "compliance officer") {
  return new Request("http://localhost/api/search/query-intent", {
    method: "POST",
    headers: { "content-type": "application/json", "x-real-ip": "test-ip" },
    body: JSON.stringify({ query, locale: "en" }),
  });
}

describe("search-query routing HTTP boundary", () => {
  beforeEach(() => {
    vi.stubEnv("SEARCH_QUERY_JEV_ENABLED", "true");
    burst.mockReset().mockResolvedValue({ success: true, reset: Date.now() + 60_000 });
    sustained.mockReset().mockResolvedValue({ success: true, reset: Date.now() + 60_000 });
    propose.mockReset().mockResolvedValue({ version: "jev-search-catalog5-v1", keywords: [] });
  });
  afterEach(() => vi.unstubAllEnvs());

  it("keeps the paid route disabled by default", async () => {
    vi.stubEnv("SEARCH_QUERY_JEV_ENABLED", "false");
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect(burst).not.toHaveBeenCalled();
    expect(propose).not.toHaveBeenCalled();
  });

  it("rejects oversized input before rate limiting or model spend", async () => {
    const response = await POST(request("x".repeat(181)));
    expect(response.status).toBe(400);
    expect(burst).not.toHaveBeenCalled();
    expect(propose).not.toHaveBeenCalled();
  });

  it("fails closed when the paid-query limiter rejects the request", async () => {
    burst.mockResolvedValue({ success: false, reset: Date.now() + 5_000 });
    const response = await POST(request());
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBeTruthy();
    expect(propose).not.toHaveBeenCalled();
  });

  it("returns one private configuration and timing on success", async () => {
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("server-timing")).toContain("query-intent;dur=");
    expect(propose).toHaveBeenCalledTimes(1);
    expect(await response.json()).toEqual({ version: "jev-search-catalog5-v1", keywords: [] });
  });
});
