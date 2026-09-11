import { describe, expect, it } from "vitest";

import { parseOfflineSearchFilters } from "@/lib/search/offline-filters";

describe("parseOfflineSearchFilters", () => {
  it("preserves a legacy internship-only filter as unresolved Intern seniority", () => {
    expect(parseOfflineSearchFilters({ etype: "internship" })).toMatchObject({
      employmentTypes: [],
      unresolvedExplicitSlugs: { sen: ["intern"] },
    });
  });

  it("keeps valid values from a mixed legacy employment-type filter", () => {
    const parsed = parseOfflineSearchFilters({
      etype: "full_time,internship",
    });

    expect(parsed.employmentTypes).toEqual(["full_time"]);
    expect(parsed.unresolvedExplicitSlugs).toBeUndefined();
  });

  it("keeps an explicit seniority authoritative over a legacy internship token", () => {
    expect(
      parseOfflineSearchFilters({ etype: "internship", sen: "senior" }),
    ).toMatchObject({
      employmentTypes: [],
      unresolvedExplicitSlugs: { sen: ["senior"] },
    });
  });
});
