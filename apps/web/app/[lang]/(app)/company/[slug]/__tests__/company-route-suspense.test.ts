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

  it("shares one cache-stable company snapshot between metadata and the page body", () => {
    const source = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );

    expect(source).toContain("async function getCompanyRouteSnapshot");
    expect(source).toContain('return fetchCompanyPageDefaults({ slug, locale });');
    expect(source.match(/getCompanyRouteSnapshot\(slug, locale\)/g)).toHaveLength(2);
    expect(source).not.toContain("getCompanyBySlug(slug, locale)");
  });

  it("renders the missing-company fallback with the requested locale and slug", () => {
    const source = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );

    expect(source).toContain("async function CompanyNotFound({ locale, slug }");
    expect(source).toContain("const { i18n } = await loadCatalog(locale);");
    expect(source).toContain(
      "return <CompanyNotFound locale={locale} slug={slug} />;",
    );
    expect(source).not.toContain("loadCatalog(defaultLocale);");
  });
});
