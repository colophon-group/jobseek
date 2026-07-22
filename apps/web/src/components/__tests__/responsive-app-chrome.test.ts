import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

describe("responsive app chrome", () => {
  it("keeps informational banners in document flow on desktop", () => {
    for (const path of [
      "src/components/CookieBanner.tsx",
      "src/components/watchlist/watchlist-tip-banner.tsx",
    ]) {
      const source = readFileSync(path, "utf8");
      expect(source).toContain("bottom-14");
      expect(source).toContain("md:static");
      expect(source).not.toContain("md:fixed md:top-12");
    }
  });

  it("gives every mobile navigation item a bounded two-line label", () => {
    const source = readFileSync("src/components/AppHeader.tsx", "utf8");

    expect(source).toContain("h-14 items-center");
    expect(source).toContain("line-clamp-2 min-h-5 max-w-full text-center");
    expect(source).toContain("h-full min-w-0 flex-1");
  });
});
