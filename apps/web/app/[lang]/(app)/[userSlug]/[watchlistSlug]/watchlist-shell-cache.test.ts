import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("private watchlist cache isolation (#8368)", () => {
  it("keeps authenticated route state out of shared cache boundaries", () => {
    const legacyRouteSource = readFileSync(
      "app/[lang]/(app)/[userSlug]/[watchlistSlug]/route.ts",
      "utf8",
    );
    const loaderSource = readFileSync(
      "app/[lang]/(app)/watchlists/watchlists-loader.tsx",
      "utf8",
    );

    expect(legacyRouteSource).toContain('"Cache-Control": "private, no-store"');
    expect(legacyRouteSource).not.toContain('"use cache"');
    expect(legacyRouteSource).not.toContain("cached(");
    expect(loaderSource).not.toContain('"use cache"');
    expect(loaderSource).not.toContain("cached(");
    expect(loaderSource).not.toContain("Redis");
  });
});
