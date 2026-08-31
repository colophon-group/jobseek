import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const serviceSource = readFileSync("src/lib/services/watchlists.ts", "utf8");
const loaderSource = readFileSync(
  "app/[lang]/(app)/watchlists/watchlists-loader.tsx",
  "utf8",
);
const actionSource = readFileSync(
  "src/lib/actions/watchlist-selection.ts",
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

  it("resolves selection through an exact owner predicate", () => {
    expect(serviceSource).toContain("WHERE w.user_id = ${userId} AND ${predicate}");
    expect(serviceSource).toContain("sql`w.id = ${watchlistId}`");
    expect(loaderSource).toContain("getOwnedWatchlistById(hintedId, session.user.id)");
  });

  it("does not load public or popular data on the canonical route", () => {
    expect(loaderSource).not.toContain("getPopularWatchlists");
    expect(loaderSource).not.toContain("getPublicWatchlist");
    expect(loaderSource).not.toContain("PublicWatchlistSearch");
  });

  it("keeps user-bound selection reads and writes out of shared caches", () => {
    for (const source of [loaderSource, actionSource]) {
      expect(source).not.toContain('"use cache"');
      expect(source).not.toContain("cached(");
      expect(source).not.toContain("cacheLife(");
      expect(source).not.toContain("cacheTag(");
    }
  });

  it("clears on session termination and invalidates other tabs on sign-out", () => {
    expect(authSource).toContain("ctx.setCookie(WATCHLIST_SELECTION_COOKIE, \"\"");
    expect(authSource).toContain("maxAge: 0");
    expect(headerSource).toContain("broadcastWatchlistSelectionChanged();");
  });
});
