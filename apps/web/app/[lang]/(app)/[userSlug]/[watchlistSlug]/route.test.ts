import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  getOwned: vi.fn(),
  encode: vi.fn((_userId: string, _watchlistId: string) => "signed-selection"),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSession: () => mocks.getSession(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getOwnedWatchlistByLegacyPath: (
    userSlug: string,
    watchlistSlug: string,
    userId: string,
  ) => mocks.getOwned(userSlug, watchlistSlug, userId),
}));

vi.mock("@/lib/watchlist-selection", () => ({
  WATCHLIST_SELECTION_COOKIE: "jobseek.watchlist-selection",
  encodeWatchlistSelection: (userId: string, watchlistId: string) =>
    mocks.encode(userId, watchlistId),
  watchlistSelectionCookieOptions: {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: 100,
  },
}));

import { GET } from "./route";

const WATCHLIST_ID = "11111111-1111-4111-8111-111111111111";

function context() {
  return {
    params: Promise.resolve({
      lang: "de",
      userSlug: "alice",
      watchlistSlug: "engineering",
    }),
  };
}

describe("legacy watchlist route", () => {
  beforeEach(() => vi.clearAllMocks());

  it("gives anonymous and cross-owner requests the same privacy-safe 404", async () => {
    mocks.getSession.mockResolvedValueOnce(null).mockResolvedValueOnce({
      user: { id: "user-2" },
    });
    mocks.getOwned.mockResolvedValue(null);

    const anonymous = await GET(
      new NextRequest("https://jseek.co/de/alice/engineering"),
      context(),
    );
    const crossOwner = await GET(
      new NextRequest("https://jseek.co/de/alice/engineering"),
      context(),
    );

    for (const response of [anonymous, crossOwner]) {
      expect(response.status).toBe(404);
      expect(response.headers.get("cache-control")).toBe("private, no-store");
      expect(response.headers.get("x-robots-tag")).toBe("noindex, follow");
    }
    expect(await anonymous.text()).toBe(await crossOwner.text());
    expect(mocks.getOwned).toHaveBeenCalledOnce();
    expect(mocks.getOwned).toHaveBeenCalledWith(
      "alice",
      "engineering",
      "user-2",
    );
  });

  it("redirects only the verified owner and selects the destination", async () => {
    mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.getOwned.mockResolvedValue({ id: WATCHLIST_ID });

    const response = await GET(
      new NextRequest("https://jseek.co/de/alice/engineering"),
      context(),
    );

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://jseek.co/de/watchlists");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.cookies.get("jobseek.watchlist-selection")?.value)
      .toBe("signed-selection");
    expect(mocks.encode).toHaveBeenCalledWith("user-1", WATCHLIST_ID);
  });
});
