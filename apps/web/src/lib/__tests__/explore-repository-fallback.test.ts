import { describe, expect, it } from "vitest";

import {
  getExploreRepositoryFallbackCompanies,
  hasTypesenseSearchConfiguration,
} from "@/lib/explore-repository-fallback";

describe("Explore repository fallback", () => {
  it("contains only stable real company identity and link fields", () => {
    const companies = getExploreRepositoryFallbackCompanies();

    expect(companies).toHaveLength(10);
    expect(new Set(companies.map((company) => company.slug)).size).toBe(companies.length);
    for (const company of companies) {
      expect(Object.keys(company).sort()).toEqual(["name", "slug"]);
      expect(company.name.length).toBeGreaterThan(0);
      expect(company.slug).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/u);
      expect(company).not.toHaveProperty("id");
      expect(company).not.toHaveProperty("icon");
      expect(company).not.toHaveProperty("postings");
      expect(company).not.toHaveProperty("activeMatches");
      expect(company).not.toHaveProperty("yearMatches");
    }
  });

  it("requires every server-side Typesense search setting", () => {
    const configured = {
      TYPESENSE_HOST: "typesense.example.test",
      TYPESENSE_PORT: "443",
      TYPESENSE_PROTOCOL: "https",
      TYPESENSE_SEARCH_KEY: "search-key",
    };

    expect(hasTypesenseSearchConfiguration(configured)).toBe(true);
    for (const key of Object.keys(configured)) {
      expect(
        hasTypesenseSearchConfiguration({ ...configured, [key]: "" }),
      ).toBe(false);
    }
  });
});
