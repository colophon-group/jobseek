import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("public watchlist scanner path guard", () => {
  it("guards metadata and the page before any watchlist data lookup", () => {
    const source = readFileSync(
      "app/[lang]/(app)/[userSlug]/[watchlistSlug]/page.tsx",
      "utf8",
    );
    const metadataStart = source.indexOf("export async function generateMetadata");
    const metadataGuard = source.indexOf(
      "if (!isPlausiblePublicWatchlistPath(userSlug, watchlistSlug))",
      metadataStart,
    );
    const metadataLookup = source.indexOf(
      "getPublicWatchlistByUserAndSlug(userSlug, watchlistSlug)",
      metadataStart,
    );
    expect(metadataGuard).toBeGreaterThan(metadataStart);
    expect(metadataGuard).toBeLessThan(metadataLookup);
    expect(source.slice(metadataGuard, metadataLookup)).toContain("notFound();");

    const routeStart = source.indexOf("export default async function WatchlistRoute");
    const routeGuard = source.indexOf(
      "if (!isPlausiblePublicWatchlistPath(userSlug, watchlistSlug)) notFound();",
      routeStart,
    );
    const routeLookup = source.indexOf("getWatchlistRouteSnapshot(", routeStart);
    expect(routeGuard).toBeGreaterThan(routeStart);
    expect(routeGuard).toBeLessThan(routeLookup);
  });

  it("guards the OG route before its data lookup and font read", () => {
    const source = readFileSync(
      "app/[lang]/(app)/[userSlug]/[watchlistSlug]/opengraph-image.tsx",
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
