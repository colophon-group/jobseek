import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserId: vi.fn(),
  getUserWatchlistCounts: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/lib/services/watchlists", () => ({
  getUserWatchlistCounts: mocks.getUserWatchlistCounts,
}));

import { GET } from "../route";

describe("GET /api/web/watchlists/counts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserId.mockResolvedValue("user-1");
    mocks.getUserWatchlistCounts.mockResolvedValue({ "watchlist-1": 42 });
  });

  it("requires an authenticated session", async () => {
    mocks.getSessionUserId.mockResolvedValueOnce(null);

    const response = await GET(
      new Request("https://jseek.co/api/web/watchlists/counts?locale=en"),
    );

    expect(response.status).toBe(401);
    expect(mocks.getUserWatchlistCounts).not.toHaveBeenCalled();
  });

  it("returns private no-store counts in a supported locale", async () => {
    const response = await GET(
      new Request("https://jseek.co/api/web/watchlists/counts?locale=de"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.json()).toEqual({ counts: { "watchlist-1": 42 } });
    expect(mocks.getUserWatchlistCounts).toHaveBeenCalledWith("de");
  });

  it("falls back to English for an unsupported locale", async () => {
    await GET(new Request("https://jseek.co/api/web/watchlists/counts?locale=xx"));

    expect(mocks.getUserWatchlistCounts).toHaveBeenCalledWith("en");
  });
});
