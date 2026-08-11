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
    expect(source).toContain("initialPage={initialData.similarCompanies}");
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

  it("routes a missing company to the localized recovery boundary", () => {
    const source = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );
    const recoverySource = readFileSync(
      "app/[lang]/(app)/%5Fmissing/company/[slug]/page.tsx",
      "utf8",
    );

    expect(source.match(/if \(!(?:snapshot|initialData)\) notFound\(\);/g)).toHaveLength(2);
    expect(recoverySource).toContain("const { lang, slug } = await params;");
    expect(recoverySource).toContain(
      "const locale = isLocale(lang) ? lang : defaultLocale;",
    );
    expect(recoverySource).toContain("locale={locale}");
    expect(recoverySource).toContain("slug={slug}");
  });
});
