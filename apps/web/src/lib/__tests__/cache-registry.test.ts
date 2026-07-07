import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  CACHE_NAMESPACE,
  CACHE_PREFIXES_INVALIDATED_ON_SYNC,
  CACHE_TAGS_INVALIDATED_ON_SYNC,
  buildCacheKey,
  cachePrefix,
  companyDetailCacheKey,
} from "@/lib/cache-registry";

describe("cache registry", () => {
  it("declares the crawler-sync Redis prefix sweep in one place", () => {
    expect(CACHE_PREFIXES_INVALIDATED_ON_SYNC).toEqual([
      "loc-suggest:",
      "occ-suggest:",
      "sen-suggest:",
      "tech-suggest:",
      "company-suggest:",
      "company-slug:",
      "company-similar:",
    ]);
  });

  it("declares the crawler-sync use-cache tag sweep in one place", () => {
    expect(CACHE_TAGS_INVALIDATED_ON_SYNC).toEqual([
      "typeahead:locations",
      "typeahead:occupations",
      "typeahead:seniorities",
      "typeahead:technologies",
      "typeahead:companies",
      "company-csv-data",
    ]);
  });

  it("builds cache keys from shared namespaces", () => {
    expect(cachePrefix(CACHE_NAMESPACE.COMPANY_DETAIL)).toBe("company-slug:");
    expect(buildCacheKey(CACHE_NAMESPACE.COMPANY_DETAIL, "acme", "en")).toBe(
      "company-slug:acme:en",
    );
    expect(companyDetailCacheKey("acme", "en")).toBe("company-slug:acme:en");
  });

  it("keeps the route as a consumer instead of the prefix owner", () => {
    const routeSource = readFileSync(
      join(process.cwd(), "app/api/internal/invalidate-typeahead/route.ts"),
      "utf8",
    );
    expect(routeSource).toContain("CACHE_PREFIXES_INVALIDATED_ON_SYNC");
    expect(routeSource).not.toContain("const TYPEAHEAD_PREFIXES");
    expect(routeSource).not.toContain('"loc-suggest:"');
  });
});
