import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  decodeWatchlistSelection,
  encodeWatchlistSelection,
  WATCHLIST_SELECTION_MAX_AGE,
  watchlistSelectionCookieOptions,
} from "@/lib/watchlist-selection";

const WATCHLIST_ID = "11111111-1111-4111-8111-111111111111";
const SECRET = "test-only-selection-secret";

describe("watchlist selection cookie", () => {
  it("round-trips only for the user it was issued to", () => {
    const value = encodeWatchlistSelection("user-a", WATCHLIST_ID, SECRET);
    expect(value).not.toContain(WATCHLIST_ID);
    expect(decodeWatchlistSelection(value, "user-a", SECRET)).toBe(WATCHLIST_ID);
    expect(decodeWatchlistSelection(value, "user-b", SECRET)).toBeNull();
  });

  it("uses randomized opaque tokens and rejects retired raw-id versions", () => {
    const first = encodeWatchlistSelection("user-a", WATCHLIST_ID, SECRET);
    const second = encodeWatchlistSelection("user-a", WATCHLIST_ID, SECRET);
    expect(first).not.toBe(second);
    expect(decodeWatchlistSelection(
      `v1.${WATCHLIST_ID}.old-signature`,
      "user-a",
      SECRET,
    )).toBeNull();
  });

  it("rejects tampering, unknown versions, and malformed ciphertext", () => {
    const value = encodeWatchlistSelection("user-a", WATCHLIST_ID, SECRET);
    expect(decodeWatchlistSelection(`${value}x`, "user-a", SECRET)).toBeNull();
    expect(decodeWatchlistSelection(value.replace(/^v2/, "v3"), "user-a", SECRET))
      .toBeNull();
    expect(decodeWatchlistSelection("v2.nonce.ciphertext.signature", "user-a", SECRET))
      .toBeNull();
  });

  it("exports the real private cookie policy", () => {
    expect(watchlistSelectionCookieOptions).toEqual({
      httpOnly: true,
      sameSite: "lax",
      secure: false,
      path: "/",
      maxAge: WATCHLIST_SELECTION_MAX_AGE,
    });
  });
});
