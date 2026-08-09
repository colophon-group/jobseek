import { describe, expect, it } from "vitest";

import { comparePostingSnapshot } from "../../../scripts/verify-saved-job-typesense-coverage";

const source = {
  postingId: "posting-1",
  title: "Engineer",
  sourceUrl: "https://example.com/jobs/1",
  firstSeenAt: 1_767_225_600,
  isActive: true,
  salaryMin: 120_000,
  salaryMax: null,
  salaryCurrency: "CHF",
  salaryPeriod: null,
  companyId: "company-1",
  companyName: "Example",
  companySlug: "example",
  companyIcon: null,
};

const document = {
  id: "posting-1",
  title: "Engineer",
  source_url: "https://example.com/jobs/1",
  first_seen_at: 1_767_225_600,
  is_active: true,
  salary_min: 120_000,
  salary_currency: "CHF",
  company_id: "company-1",
  company_name: "Example",
  company_slug: "example",
};

describe("saved-job Typesense coverage verifier", () => {
  it("accepts exact required fields and omitted null optionals", () => {
    expect(comparePostingSnapshot(source, document)).toEqual([]);
  });

  it("reports incomplete and divergent snapshot fields", () => {
    expect(
      comparePostingSnapshot(source, {
        ...document,
        title: "",
        salary_min: 110_000,
        salary_period: 12,
        company_slug: "other",
      }),
    ).toEqual(["title", "company_slug", "salary_min", "salary_period"]);
  });
});
