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

  it("invalidates cached missing snapshots immediately after crawler sync", () => {
    const source = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );

    expect(source).toContain("companyCsvDataCacheTag");
    expect(source.match(/cacheTag\(companyCsvDataCacheTag\(\)\)/g)).toHaveLength(3);
  });

  it("keeps the noindex company shell on the shared one-day cache tier", () => {
    const routeSource = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );
    const ttlSource = readFileSync("src/lib/cache-ttl.ts", "utf8");

    expect(routeSource.match(/cacheLife\(\{ revalidate: CACHE_TTL_COMPANY_SHELL \}\)/g))
      .toHaveLength(3);
    expect(ttlSource).toContain(
      "export const CACHE_TTL_COMPANY_SHELL = CACHE_TTL_DAY;",
    );
    expect(ttlSource).toContain("export const CACHE_TTL_DAY = 86400;");
    expect(routeSource).not.toContain("company.activeJobCount");
  });

  it("routes a missing company to the localized recovery boundary", () => {
    const source = readFileSync(
      "app/[lang]/(app)/company/[slug]/page.tsx",
      "utf8",
    );
    const recoverySource = readFileSync(
      "app/[lang]/(app)/company/[slug]/not-found.tsx",
      "utf8",
    );

    expect(source.match(/if \(!(?:snapshot|initialData)\) notFound\(\);/g)).toHaveLength(2);
    expect(recoverySource).toContain("const { lang, slug } = useParams");
    expect(recoverySource).toContain("locale={lang}");
    expect(recoverySource).toContain("slug={slug}");
  });
});
