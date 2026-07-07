import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const STATE_REFS = [
  "keywordsRef",
  "locationsRef",
  "occupationsRef",
  "senioritiesRef",
  "technologiesRef",
  "employmentTypesRef",
  "workModeRef",
  "salaryCurrencyRef",
  "salaryMinRef",
  "salaryMaxRef",
  "experienceMinRef",
  "experienceMaxRef",
  "showPostingIdRef",
  "companiesRef",
  "totalCompaniesRef",
  "isDegradedRef",
];

function readPageSource(relativePath: string) {
  return readFileSync(join(process.cwd(), relativePath), "utf8");
}

describe("latest-state page source structure (#3096)", () => {
  it("keeps SearchPage and CompanyPage state mirrors behind useLatestState", () => {
    const searchPage = readPageSource("app/[lang]/(app)/explore/search-page.tsx");
    const companyPage = readPageSource("app/[lang]/(app)/company/[slug]/company-page.tsx");

    for (const source of [searchPage, companyPage]) {
      expect(source).toContain("useLatestState");
      for (const refName of STATE_REFS) {
        expect(source).not.toContain(`${refName}.current =`);
      }
    }
  });
});
