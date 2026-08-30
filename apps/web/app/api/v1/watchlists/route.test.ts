import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: {
    limit: vi.fn(async () => {
      throw new Error("no redis in unit tests");
    }),
  },
  getClientIp: () => "test-ip",
}));

const mocks = vi.hoisted(() => ({
  logExternalError: vi.fn(),
  searchPublicWatchlists: vi.fn(),
  getPopularWatchlists: vi.fn(),
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

vi.mock("@/lib/services/watchlists", () => ({
  searchPublicWatchlists: mocks.searchPublicWatchlists,
  getPopularWatchlists: mocks.getPopularWatchlists,
}));

import { GET } from "./route";

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/watchlists${qs}`);
}

describe("GET /api/v1/watchlists locale contract (#6132)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.searchPublicWatchlists.mockResolvedValue({ watchlists: [], total: 0 });
    mocks.getPopularWatchlists.mockResolvedValue({ watchlists: [], total: 0 });
  });

  it("rejects unsupported locales before querying or constructing links", async () => {
    const res = await GET(makeReq("?q=engineering&locale=xx"));
    const body = (await res.json()) as { error?: string };

    expect(res.status).toBe(400);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(body.error).toBe("Invalid 'locale' param. Supported: en, de, fr, it");
    expect(mocks.searchPublicWatchlists).not.toHaveBeenCalled();
    expect(mocks.getPopularWatchlists).not.toHaveBeenCalled();
  });

  it("requests strict provider handling for cacheable successes", async () => {
    const res = await GET(makeReq("?q=engineering&locale=en"));

    expect(res.status).toBe(200);
    expect(mocks.searchPublicWatchlists).toHaveBeenCalledWith({
      query: "engineering",
      offset: 0,
      limit: 10,
      locale: "en",
      failOnUnavailable: true,
    });
  });

  it("returns a non-cacheable safe error when Typesense is unavailable", async () => {
    const providerError = new Error("provider-internal-canary-do-not-expose");
    mocks.getPopularWatchlists.mockRejectedValue(providerError);

    const res = await GET(makeReq("?locale=en"));
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body).toEqual({ error: "Search service unavailable" });
    expect(JSON.stringify(body)).not.toContain("provider-internal-canary");
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      { service: "typesense", operation: "public_api_watchlists" },
      providerError,
    );
  });
});
