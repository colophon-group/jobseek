import { describe, expect, it } from "vitest";

import {
  authorizeWatchlistCopySource,
  WATCHLIST_COPY_SOURCE_KINDS,
} from "@/lib/watchlist-copy-policy";

describe("watchlist copy source policy", () => {
  it("authorizes owner duplication without consulting visibility", () => {
    expect(authorizeWatchlistCopySource({
      userId: "owner-1",
    }, "owner-1")).toEqual({ sourceKind: "owned" });
  });

  it("fails closed for a cross-user source even if a legacy public signal is present", () => {
    const legacyPublicSource = {
      userId: "owner-1",
      isPublic: true,
    };
    expect(authorizeWatchlistCopySource(legacyPublicSource, "owner-2")).toBeNull();
  });

  it.each(["grant", "share", "template"] as const)(
    "keeps the future %s policy dormant until real authorization exists",
    (sourceKind) => {
      expect(authorizeWatchlistCopySource(
        { userId: "owner-1" },
        "owner-2",
        sourceKind,
      )).toBeNull();
    },
  );

  it("enumerates the reviewed source-kind seam", () => {
    expect(WATCHLIST_COPY_SOURCE_KINDS).toEqual([
      "owned",
      "grant",
      "share",
      "template",
    ]);
  });
});
