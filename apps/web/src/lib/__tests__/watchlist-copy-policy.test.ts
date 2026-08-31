import { describe, expect, it } from "vitest";

import { canCopyWatchlistSource } from "@/lib/watchlist-copy-policy";

describe("watchlist copy source policy", () => {
  it("allows an owner to duplicate a private watchlist", () => {
    expect(canCopyWatchlistSource({
      userId: "owner-1",
      isPublic: false,
    }, "owner-1")).toBe(true);
  });

  it("preserves cross-user copying for currently shareable public sources", () => {
    expect(canCopyWatchlistSource({
      userId: "owner-1",
      isPublic: true,
    }, "owner-2")).toBe(true);
  });

  it("does not infer cross-user access to a private source", () => {
    expect(canCopyWatchlistSource({
      userId: "owner-1",
      isPublic: false,
    }, "owner-2")).toBe(false);
  });
});
