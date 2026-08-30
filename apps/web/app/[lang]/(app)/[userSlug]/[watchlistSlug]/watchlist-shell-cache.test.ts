import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("public watchlist shell cache (#8258)", () => {
  it("uses the one-hour shell tier while retaining exact tag invalidation", () => {
    const routeSource = readFileSync(
      "app/[lang]/(app)/[userSlug]/[watchlistSlug]/page.tsx",
      "utf8",
    );
    const ttlSource = readFileSync("src/lib/cache-ttl.ts", "utf8");

    expect(routeSource).toContain(
      "cacheLife({ revalidate: CACHE_TTL_WATCHLIST_SHELL });",
    );
    expect(routeSource).toContain(
      "cacheTag(watchlistCacheTag(userSlug, watchlistSlug));",
    );
    expect(ttlSource).toContain(
      "export const CACHE_TTL_WATCHLIST_SHELL = CACHE_TTL_LONG;",
    );
    expect(ttlSource).toContain("export const CACHE_TTL_LONG = 3600;");
  });
});
