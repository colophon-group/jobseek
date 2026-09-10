import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const serviceSource = readFileSync("src/lib/services/watchlists.ts", "utf8");
const loaderSource = readFileSync(
  "app/[lang]/(app)/watchlists/watchlists-loader.tsx",
  "utf8",
);
const detailLoaderSource = readFileSync(
  "app/[lang]/(app)/watchlists/[watchlistId]/owned-watchlist-loader.tsx",
  "utf8",
);
const authSource = readFileSync("src/lib/auth.ts", "utf8");
const headerSource = readFileSync("src/components/AppHeader.tsx", "utf8");

describe("private watchlist route contract", () => {
  it("keeps fallback ordering deterministic", () => {
    expect(serviceSource).toContain(
      "ORDER BY w.last_accessed_at DESC, w.created_at DESC, w.id ASC",
    );
  });

  it("resolves direct UUID routes through an exact owner predicate", () => {
    expect(serviceSource).toContain("sql`w.user_id = ${userId} AND ${predicate}`");
    expect(serviceSource).toContain("sql`w.id = ${normalizedWatchlistId}`");
    expect(detailLoaderSource).toContain(
      "getOwnedWatchlistById(watchlistId, session.user.id)",
    );
    expect(detailLoaderSource).toContain(
      'isWatchlistId } from "@/lib/watchlist-id"',
    );
  });

  it("does not load public or popular data on the canonical route", () => {
    expect(loaderSource).not.toContain("getPopularWatchlists");
    expect(loaderSource).not.toContain("getPublicWatchlist");
    expect(loaderSource).not.toContain("PublicWatchlistSearch");
  });

  it("does not retain selection-cookie or cross-tab channel coupling", () => {
    for (const source of [loaderSource, detailLoaderSource, authSource, headerSource]) {
      expect(source).not.toContain("WATCHLIST_SELECTION_COOKIE");
      expect(source).not.toContain("watchlist-selection-client");
      expect(source).not.toContain("broadcastWatchlistSelectionChanged");
    }
  });
});
