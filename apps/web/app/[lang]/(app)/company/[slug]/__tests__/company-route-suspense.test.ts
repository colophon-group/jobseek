import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

describe("company route partial prerendering", () => {
  it("places every useSearchParams client subtree behind an explicit Suspense boundary", () => {
    const source = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );

    expect(source).toContain("<Suspense fallback={null}>");
    expect(source).toContain("<SimilarSection");
    expect(source).toContain("<Suspense fallback={<CompanySkeleton />}>");
    expect(source).toContain("<CompanyContent");
  });
});
