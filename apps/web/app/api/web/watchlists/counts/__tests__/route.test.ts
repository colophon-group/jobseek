import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserIdFromHeaders: vi.fn(),
  getUserWatchlistCountsForUser: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserIdFromHeaders: mocks.getSessionUserIdFromHeaders,
}));

vi.mock("@/lib/services/watchlists", () => ({
  getUserWatchlistCountsForUser: mocks.getUserWatchlistCountsForUser,
}));

import { GET } from "../route";

describe("GET /api/web/watchlists/counts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserIdFromHeaders.mockResolvedValue("user-1");
    mocks.getUserWatchlistCountsForUser.mockResolvedValue({ "watchlist-1": 42 });
  });

  it("requires an authenticated session", async () => {
    mocks.getSessionUserIdFromHeaders.mockResolvedValueOnce(null);

    const response = await GET(
      new Request("https://jseek.co/api/web/watchlists/counts?locale=en"),
    );

    expect(response.status).toBe(401);
    expect(mocks.getUserWatchlistCountsForUser).not.toHaveBeenCalled();
  });

  it("returns private no-store counts in a supported locale", async () => {
    const request = new Request(
      "https://jseek.co/api/web/watchlists/counts?locale=de",
      { headers: { cookie: "better-auth.session_token=route-token" } },
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.json()).toEqual({ counts: { "watchlist-1": 42 } });
    expect(mocks.getSessionUserIdFromHeaders).toHaveBeenCalledWith(
      request.headers,
    );
    expect(mocks.getUserWatchlistCountsForUser).toHaveBeenCalledWith(
      "user-1",
      "de",
    );
  });

  it("falls back to English for an unsupported locale", async () => {
    await GET(new Request("https://jseek.co/api/web/watchlists/counts?locale=xx"));

    expect(mocks.getUserWatchlistCountsForUser).toHaveBeenCalledWith(
      "user-1",
      "en",
    );
  });
});
