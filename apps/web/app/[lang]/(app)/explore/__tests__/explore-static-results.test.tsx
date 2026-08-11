import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/test-utils/lingui-mock";
import type { ExploreData } from "@/lib/actions/explore-page-data";
import { ExploreStaticResults } from "../explore-static-results";

function makeData(result: ExploreData["result"]): ExploreData {
  return {
    result,
    parsed: {
      keywords: [],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
    },
    displayCurrency: "EUR",
    jobLanguages: [],
    languages: ["en"],
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: "EUR",
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
  };
}

describe("ExploreStaticResults", () => {
  it("renders accessible retry guidance for a configured degraded provider", () => {
    const { container } = render(
      <ExploreStaticResults
        locale="en"
        heading="Explore Jobs"
        data={makeData({ companies: [], totalCompanies: 0, degraded: true })}
      />,
    );

    expect(screen.getByRole("alert").textContent).toMatch(/try refreshing the page/i);
    expect(container.querySelector("[data-explore-repository-fallback]")).toBeNull();
    expect(container.querySelector("[data-search-result-company]")).toBeNull();
  });

  it("does not relabel a legitimate configured zero result as degraded", () => {
    const { container } = render(
      <ExploreStaticResults
        locale="en"
        heading="Explore Jobs"
        data={makeData({ companies: [], totalCompanies: 0 })}
      />,
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector("[data-explore-repository-fallback]")).toBeNull();
  });
});
