import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const serviceSource = readFileSync("src/lib/services/watchlists.ts", "utf8");
const loaderSource = readFileSync(
  "app/[lang]/(app)/watchlists/watchlists-loader.tsx",
  "utf8",
);

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
});
