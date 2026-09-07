import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  apiLimit: vi.fn(),
  searchPublicWatchlists: vi.fn(),
  getPopularWatchlists: vi.fn(),
}));

vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: { limit: mocks.apiLimit },
  getClientIp: () => "test-ip",
}));

vi.mock("@/lib/services/watchlists", () => ({
  searchPublicWatchlists: mocks.searchPublicWatchlists,
  getPopularWatchlists: mocks.getPopularWatchlists,
}));

import { GET } from "./route";

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/watchlists${qs}`);
}

describe("GET /api/v1/watchlists retirement contract (#8367)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each(["", "?q=engineering&locale=en", "?q=private-canary&locale=xx"])(
    "returns the same bounded non-cacheable 410 for %s",
    async (query) => {
      const res = await GET(makeReq(query));
      const body = await res.json();

      expect(res.status).toBe(410);
      expect(body).toEqual({
        error: "Public watchlist discovery is no longer available",
      });
      expect(JSON.stringify(body)).not.toContain("private-canary");
      expect(res.headers.get("Sunset")).toBe(
        "Sat, 31 Oct 2026 23:59:59 GMT",
      );
      expect(res.headers.get("Cache-Control")).toBe("no-store");
      expect(res.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
      expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    },
  );

  it("does not rate-limit, search, or read watchlists while serving the compatibility response", async () => {
    const res = await GET(makeReq("?q=anything&locale=de"));

    expect(res.status).toBe(410);
    expect(mocks.apiLimit).not.toHaveBeenCalled();
    expect(mocks.searchPublicWatchlists).not.toHaveBeenCalled();
    expect(mocks.getPopularWatchlists).not.toHaveBeenCalled();
  });
});
