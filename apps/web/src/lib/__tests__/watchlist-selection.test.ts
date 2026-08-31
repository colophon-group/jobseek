import { describe, expect, it } from "vitest";
import { vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  decodeWatchlistSelection,
  encodeWatchlistSelection,
} from "@/lib/watchlist-selection";

const WATCHLIST_ID = "11111111-1111-4111-8111-111111111111";
const SECRET = "test-only-selection-secret";

describe("watchlist selection cookie", () => {
  it("round-trips only for the user it was issued to", () => {
    const value = encodeWatchlistSelection("user-a", WATCHLIST_ID, SECRET);
    expect(decodeWatchlistSelection(value, "user-a", SECRET)).toBe(WATCHLIST_ID);
    expect(decodeWatchlistSelection(value, "user-b", SECRET)).toBeNull();
  });

  it("rejects tampering, unknown versions, and malformed ids", () => {
    const value = encodeWatchlistSelection("user-a", WATCHLIST_ID, SECRET);
    expect(decodeWatchlistSelection(`${value}x`, "user-a", SECRET)).toBeNull();
    expect(decodeWatchlistSelection(value.replace(/^v1/, "v2"), "user-a", SECRET))
      .toBeNull();
    expect(decodeWatchlistSelection("v1.not-a-uuid.signature", "user-a", SECRET))
      .toBeNull();
  });
});
