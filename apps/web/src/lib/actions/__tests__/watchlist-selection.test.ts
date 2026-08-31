import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getUserId: vi.fn(),
  getOwned: vi.fn(),
  setCookie: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("next/headers", () => ({
  cookies: async () => ({ set: mocks.setCookie }),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: () => mocks.getUserId(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getOwnedWatchlistById: (watchlistId: string, userId: string) =>
    mocks.getOwned(watchlistId, userId),
}));

import { selectOwnedWatchlist } from "@/lib/actions/watchlist-selection";
import {
  decodeWatchlistSelection,
  WATCHLIST_SELECTION_COOKIE,
  WATCHLIST_SELECTION_MAX_AGE,
} from "@/lib/watchlist-selection";

const WATCHLIST_ID = "11111111-1111-4111-8111-111111111111";

describe("selectOwnedWatchlist", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("BETTER_AUTH_SECRET", "test-only-selection-secret");
    mocks.getUserId.mockResolvedValue("user-1");
  });

  it("writes the httpOnly hint only after exact owner validation", async () => {
    mocks.getOwned.mockResolvedValue({ id: WATCHLIST_ID });

    await expect(selectOwnedWatchlist(WATCHLIST_ID)).resolves.toEqual({ ok: true });

    expect(mocks.getOwned).toHaveBeenCalledWith(WATCHLIST_ID, "user-1");
    const [name, value, options] = mocks.setCookie.mock.calls[0];
    expect(name).toBe(WATCHLIST_SELECTION_COOKIE);
    expect(value).not.toContain(WATCHLIST_ID);
    expect(decodeWatchlistSelection(value, "user-1")).toBe(WATCHLIST_ID);
    expect(options).toEqual({
      httpOnly: true,
      sameSite: "lax",
      secure: false,
      path: "/",
      maxAge: WATCHLIST_SELECTION_MAX_AGE,
    });
  });

  it("clears the hint for a stale or cross-account id", async () => {
    mocks.getOwned.mockResolvedValue(null);

    await expect(selectOwnedWatchlist(WATCHLIST_ID)).resolves.toEqual({ ok: false });

    expect(mocks.setCookie).toHaveBeenCalledWith(
      "jobseek.watchlist-selection",
      "",
      expect.objectContaining({ maxAge: 0 }),
    );
  });
});
