import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("legacy watchlist route guard", () => {
  it("requires a session and exact owner lookup before redirecting", () => {
    const source = readFileSync(
      "app/[lang]/(app)/[userSlug]/[watchlistSlug]/route.ts",
      "utf8",
    );
    const sessionGuard = source.indexOf("if (!session) return privateNotFound(locale)");
    const ownerLookup = source.indexOf("getOwnedWatchlistByLegacyPath(");
    const redirect = source.indexOf("NextResponse.redirect(");
    expect(sessionGuard).toBeGreaterThan(0);
    expect(sessionGuard).toBeLessThan(ownerLookup);
    expect(ownerLookup).toBeLessThan(redirect);
    expect(source).not.toContain("getPublicWatchlistByUserAndSlug");
  });

  it("guards the OG route before its data lookup and font read", () => {
    const source = readFileSync(
      "app/og/watchlist/[lang]/[userSlug]/[watchlistSlug]/route.tsx",
      "utf8",
    );
    const guard = source.indexOf(
      "if (!isPlausiblePublicWatchlistPath(userSlug, watchlistSlug)) notFound();",
    );
    expect(guard).toBeGreaterThan(0);
    expect(guard).toBeLessThan(
      source.indexOf("getPublicWatchlistByUserAndSlug(userSlug, watchlistSlug)"),
    );
    expect(guard).toBeLessThan(source.indexOf("await fontPromise"));
  });
});
