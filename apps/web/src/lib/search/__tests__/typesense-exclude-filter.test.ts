import { describe, it, expect } from "vitest";
import { applyExcludeTitleFilter } from "@/lib/search/typesense";
import type { SearchResultCompany } from "@/lib/search/types";

function company(id: string, titles: string[]): SearchResultCompany {
  return {
    company: { id, name: id, slug: id, icon: null },
    activeMatches: titles.length,
    yearMatches: titles.length,
    postings: titles.map((title, i) => ({
      id: `${id}-${i}`,
      title,
      firstSeenAt: new Date(0),
      relevanceScore: 0,
      locations: [],
      isActive: true,
    })),
  };
}

describe("applyExcludeTitleFilter", () => {
  it("returns input unchanged when excludeTitles is empty", () => {
    const input = [company("c1", ["Senior Engineer"])];
    expect(applyExcludeTitleFilter(input, [])).toBe(input);
  });

  it("drops postings whose title matches any keyword (whole word)", () => {
    const input = [
      company("c1", ["Senior Engineer", "Junior Engineer", "Staff Engineer"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior", "staff"]);
    expect(result).toHaveLength(1);
    expect(result[0].postings.map((p) => p.title)).toEqual(["Junior Engineer"]);
  });

  it("preserves totals (activeMatches/yearMatches) because they are company-wide", () => {
    const input = [
      company("c1", ["Senior Engineer", "Junior Engineer", "Staff Engineer"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior"]);
    expect(result[0].activeMatches).toBe(3);
    expect(result[0].yearMatches).toBe(3);
  });

  it("drops companies whose postings all match", () => {
    const input = [
      company("c1", ["Senior Engineer", "Staff Engineer"]),
      company("c2", ["Junior Engineer"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior", "staff"]);
    expect(result.map((c) => c.company.id)).toEqual(["c2"]);
  });

  it("is case-insensitive and uses word boundaries", () => {
    const input = [
      company("c1", ["SENIOR Engineer", "Seniority Coach"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior"]);
    expect(result[0].postings.map((p) => p.title)).toEqual(["Seniority Coach"]);
  });

  it("handles null titles by keeping them (nothing to match)", () => {
    const input: SearchResultCompany[] = [
      {
        company: { id: "c1", name: "c1", slug: "c1", icon: null },
        activeMatches: 1,
        yearMatches: 1,
        postings: [
          { id: "p1", title: null, firstSeenAt: new Date(0), relevanceScore: 0, locations: [] },
        ],
      },
    ];
    const result = applyExcludeTitleFilter(input, ["senior"]);
    expect(result).toHaveLength(1);
    expect(result[0].postings).toHaveLength(1);
  });
});
