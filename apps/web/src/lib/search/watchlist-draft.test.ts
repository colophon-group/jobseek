import { describe, expect, it } from "vitest";

import { buildSearchWatchlistDraft } from "./watchlist-draft";

describe("buildSearchWatchlistDraft", () => {
  it("preserves the complete focused search and one-company scope", () => {
    expect(buildSearchWatchlistDraft({
      fallbackTitle: "My search",
      keywords: ["engineer"],
      locations: [{
        id: 1,
        slug: "switzerland",
        name: "Switzerland",
        type: "country",
        parentName: null,
      }],
      occupations: [{ slug: "software-engineer", name: "Software Engineer" }],
      seniorities: [{ slug: "senior", name: "Senior" }],
      technologies: [{ slug: "typescript", name: "TypeScript" }],
      employmentTypes: ["full-time"],
      workMode: ["hybrid"],
      salaryMin: 120_000,
      salaryCurrency: "CHF",
      experienceMin: 4,
      companyScope: { id: "company-1", name: "Acme" },
    })).toEqual({
      title: "Acme · engineer · Switzerland · Software Engineer",
      companyIds: ["company-1"],
      filters: {
        anyCompany: false,
        keywords: ["engineer"],
        locationSlugs: ["switzerland"],
        occupationSlugs: ["software-engineer"],
        senioritySlugs: ["senior"],
        technologySlugs: ["typescript"],
        employmentType: ["full-time"],
        workMode: ["hybrid"],
        salaryMin: 120_000,
        salaryCurrency: "CHF",
        experienceMin: 4,
      },
      isPublic: false,
    });
  });
});
