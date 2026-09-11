import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserIdFromHeaders: vi.fn(),
  getUserWatchlistActivityPreviewsForUser: vi.fn(),
  limit: vi.fn(),
  logExternalError: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserIdFromHeaders: mocks.getSessionUserIdFromHeaders,
}));

vi.mock("@/lib/services/watchlists", () => ({
  getUserWatchlistActivityPreviewsForUser:
    mocks.getUserWatchlistActivityPreviewsForUser,
}));

vi.mock("@/lib/rate-limit", () => ({
  watchlistActivityLimiter: { limit: mocks.limit },
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

import { GET } from "../route";

describe("GET /api/web/watchlists/counts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserIdFromHeaders.mockResolvedValue("user-1");
    mocks.limit.mockResolvedValue({
      success: true,
      reset: Date.now() + 60_000,
    });
    mocks.getUserWatchlistActivityPreviewsForUser.mockResolvedValue({
      "watchlist-1": {
        activeJobCount: 42,
        activeCompanyCount: 3,
        topCompanies: [
          { id: "company-1", name: "Acme", icon: "acme.png" },
        ],
      },
    });
  });

  it("requires an authenticated session", async () => {
    mocks.getSessionUserIdFromHeaders.mockResolvedValueOnce(null);

    const response = await GET(
      new Request("https://jseek.co/api/web/watchlists/counts?locale=en"),
    );

    expect(response.status).toBe(401);
    expect(mocks.getUserWatchlistActivityPreviewsForUser).not.toHaveBeenCalled();
    expect(mocks.limit).not.toHaveBeenCalled();
  });

  it("rate-limits authenticated activity replays before querying Typesense", async () => {
    mocks.limit.mockResolvedValueOnce({
      success: false,
      reset: Date.now() + 30_000,
    });

    const response = await GET(
      new Request("https://jseek.co/api/web/watchlists/counts?locale=en"),
    );

    expect(response.status).toBe(429);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(response.headers.get("Retry-After")).toBeTruthy();
    await expect(response.json()).resolves.toEqual({ error: "too_many_requests" });
    expect(mocks.limit).toHaveBeenCalledWith("user-1");
    expect(mocks.getUserWatchlistActivityPreviewsForUser).not.toHaveBeenCalled();
  });

  it("fails closed before exhaustive faceting when the limiter store is unavailable", async () => {
    const error = new Error("redis unavailable");
    mocks.limit.mockRejectedValueOnce(error);

    const response = await GET(
      new Request("https://jseek.co/api/web/watchlists/counts?locale=en"),
    );

    expect(response.status).toBe(503);
    expect(response.headers.get("Retry-After")).toBe("30");
    await expect(response.json()).resolves.toEqual({
      error: "temporarily_unavailable",
    });
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "warn",
      { service: "redis", operation: "watchlist_activity_rate_limit" },
      error,
    );
    expect(mocks.getUserWatchlistActivityPreviewsForUser).not.toHaveBeenCalled();
  });

  it("returns private no-store counts in a supported locale", async () => {
    const request = new Request(
      "https://jseek.co/api/web/watchlists/counts?locale=de",
      { headers: { cookie: "better-auth.session_token=route-token" } },
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.json()).toEqual({
      counts: { "watchlist-1": 42 },
      previews: {
        "watchlist-1": {
          activeJobCount: 42,
          activeCompanyCount: 3,
          topCompanies: [
            { id: "company-1", name: "Acme", icon: "acme.png" },
          ],
        },
      },
    });
    expect(mocks.getSessionUserIdFromHeaders).toHaveBeenCalledWith(
      request.headers,
    );
    expect(mocks.getUserWatchlistActivityPreviewsForUser).toHaveBeenCalledWith(
      "user-1",
      "de",
    );
  });

  it("falls back to English for an unsupported locale", async () => {
    await GET(new Request("https://jseek.co/api/web/watchlists/counts?locale=xx"));

    expect(mocks.getUserWatchlistActivityPreviewsForUser).toHaveBeenCalledWith(
      "user-1",
      "en",
    );
  });
});
