import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getUserId: vi.fn(),
  getOwned: vi.fn(),
  setCookie: vi.fn(),
  encode: vi.fn(() => "signed-selection"),
}));

vi.mock("next/headers", () => ({
  cookies: async () => ({ set: mocks.setCookie }),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: () => mocks.getUserId(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  getOwnedWatchlistById: (...args: unknown[]) => mocks.getOwned(...args),
}));

vi.mock("@/lib/watchlist-selection", () => ({
  WATCHLIST_SELECTION_COOKIE: "jobseek.watchlist-selection",
  encodeWatchlistSelection: (...args: unknown[]) => mocks.encode(...args),
  isWatchlistId: (value: string) => value.includes("-"),
  watchlistSelectionCookieOptions: {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: 100,
  },
}));

import { selectOwnedWatchlist } from "@/lib/actions/watchlist-selection";

const WATCHLIST_ID = "11111111-1111-4111-8111-111111111111";

describe("selectOwnedWatchlist", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getUserId.mockResolvedValue("user-1");
  });

  it("writes the httpOnly hint only after exact owner validation", async () => {
    mocks.getOwned.mockResolvedValue({ id: WATCHLIST_ID });

    await expect(selectOwnedWatchlist(WATCHLIST_ID)).resolves.toEqual({ ok: true });

    expect(mocks.getOwned).toHaveBeenCalledWith(WATCHLIST_ID, "user-1");
    expect(mocks.encode).toHaveBeenCalledWith("user-1", WATCHLIST_ID);
    expect(mocks.setCookie).toHaveBeenCalledWith(
      "jobseek.watchlist-selection",
      "signed-selection",
      expect.objectContaining({ httpOnly: true, sameSite: "lax", path: "/" }),
    );
  });

  it("clears the hint for a stale or cross-account id", async () => {
    mocks.getOwned.mockResolvedValue(null);

    await expect(selectOwnedWatchlist(WATCHLIST_ID)).resolves.toEqual({ ok: false });

    expect(mocks.setCookie).toHaveBeenCalledWith(
      "jobseek.watchlist-selection",
      "",
      expect.objectContaining({ maxAge: 0 }),
    );
    expect(mocks.encode).not.toHaveBeenCalled();
  });
});
