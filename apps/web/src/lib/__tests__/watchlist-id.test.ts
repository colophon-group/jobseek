import { describe, expect, it } from "vitest";

import { isWatchlistId } from "@/lib/watchlist-id";

describe("watchlist id validation", () => {
  it.each([
    "11111111-1111-4111-8111-111111111111",
    "AAAAAAAA-AAAA-8AAA-BAAA-AAAAAAAAAAAA",
  ])("accepts a canonical UUID route id: %s", (value) => {
    expect(isWatchlistId(value)).toBe(true);
  });

  it.each([
    "not-a-watchlist-id",
    "11111111-1111-4111-7111-111111111111",
    "11111111-1111-0111-8111-111111111111",
    "11111111-1111-4111-8111-11111111111",
    "11111111-1111-4111-8111-111111111111/extra",
  ])("rejects a malformed UUID route id: %s", (value) => {
    expect(isWatchlistId(value)).toBe(false);
  });
});
