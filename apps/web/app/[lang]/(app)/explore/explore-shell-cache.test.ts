import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const appDir = resolve(__dirname, "../../../..");

describe("Explore shell cache policy (#8259)", () => {
  it("uses the one-day shell lifetime backed by visible browser refresh", () => {
    const routeSource = readFileSync(resolve(__dirname, "page.tsx"), "utf8");
    const ttlSource = readFileSync(
      resolve(appDir, "src/lib/cache-ttl.ts"),
      "utf8",
    );

    expect(routeSource).toContain(
      "cacheLife({ revalidate: CACHE_TTL_EXPLORE_SHELL });",
    );
    expect(routeSource).toContain("cacheLife(EXPLORE_DEFAULTS_CACHE_LIFE);");
    expect(ttlSource).toContain(
      "export const CACHE_TTL_EXPLORE_SHELL = CACHE_TTL_DAY;",
    );
    expect(ttlSource).toContain("export const CACHE_TTL_DAY = 86400;");
  });
});
