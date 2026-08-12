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
  searchPublicWatchlists: vi.fn(),
  getPopularWatchlists: vi.fn(),
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
});
