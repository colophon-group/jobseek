import type { Metadata } from "next";
import { Suspense } from "react";
import { cacheLife, cacheTag } from "next/cache";
import { notFound } from "next/navigation";
import { isLocale, defaultLocale, loadCatalog, initI18nForPage, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { companyCacheTag, companyCsvDataCacheTag } from "@/lib/cache-tags";
import { CACHE_TTL_COMPANY_SHELL } from "@/lib/cache-ttl";
import { fetchCompanyPageDefaults } from "@/lib/actions/company-page-data";
import type { Locale } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { CompanyHead } from "./company-head";
import { CompanyContent } from "./company-content";
import { SimilarSection } from "./similar-section";
import { CompanySkeleton } from "@/components/search/company-skeleton";
import { getDirectCompanyOgUrl } from "@/lib/og/company-og-direct";

type Props = {
  params: Promise<{ lang: string; slug: string }>;
};

/**
 * Keep the dynamic route's metadata and body on one cache-stable data
 * snapshot. Next.js currently has a Cache Components/PPR resume defect when
 * async metadata and the page body independently resolve the same dynamic
 * route data: the prerendered metadata wrapper can disagree with the runtime
 * metadata boundary and React falls back to a client render. See #5911 and
 * vercel/next.js#93401.
 */
async function getCompanyRouteSnapshot(slug: string, locale: Locale) {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_COMPANY_SHELL });
  cacheTag(companyCacheTag(slug));
  cacheTag(companyCsvDataCacheTag());
  return fetchCompanyPageDefaults({ slug, locale });
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_COMPANY_SHELL });
  const { slug, lang } = await params;
  cacheTag(companyCacheTag(slug));
  cacheTag(companyCsvDataCacheTag());
  const locale = isLocale(lang) ? lang : defaultLocale;
  const [snapshot, { i18n }] = await Promise.all([
    getCompanyRouteSnapshot(slug, locale),
    loadCatalog(locale),
  ]);
  // No company = ghost slug (deleted, never existed, typo). Bare `{}`
  // would let `[lang]/layout.tsx`'s `metadata.title.default` ("Job
  // Seek") cascade and leave the URL indexable. Tag explicitly as
  // `noindex,follow` to mirror the watchlist null-detail handling.
  if (!snapshot) notFound();
  const { company } = snapshot;

  const title = i18n._({
    id: "company.meta.title",
    comment: "SEO title for a company detail page; {name} is the company name.",
    message: "Jobs at {name}",
    values: { name: company.name },
  });
  // Company shells are cached for a day, while posting counts move every few
  // hours. Keep noindex/share metadata durable instead of publishing a count
  // that can age beyond the browser-refreshed visible list.
  const countText = i18n._({
    id: "company.meta.openPositions",
    comment: "Generic SEO metadata text for positions on a company page.",
    message: "Open positions",
  });
  const description = company.description
    ? i18n._({
        id: "company.meta.descriptionWithInfo",
        comment: "SEO description for a company page; includes job count, company name, and company summary.",
        message: "{countText} at {name}. {description}",
        values: { countText, name: company.name, description: company.description },
      })
    : i18n._({
        id: "company.meta.descriptionBasic",
        comment: "SEO description for a company page when no company summary is available.",
        message: "{countText} at {name}",
        values: { countText, name: company.name },
      });
  const path = `/company/${slug}`;
  const directOgImageUrl = await getDirectCompanyOgUrl(locale, slug);

  return {
    title,
    description,
    alternates: buildAlternates(path, locale),
    // Excluded from the search index (#2821): /company/{slug} is content-wise
    // a near-duplicate of the source ATS page (jseek-authored description +
    // client-rendered postings list). At ~4k companies × 4 locales the surface
    // dilutes site-wide quality signals and risks Helpful Content / Site
    // Reputation Abuse classification. The page stays as the in-app + shared
    // product surface; `follow` keeps PageRank flowing to internal targets
    // (curated watchlists, blog) from any external links pointing here.
    robots: { index: false, follow: true },
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}${path}`,
      type: "website",
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      ...(directOgImageUrl
        ? {
            images: [{
              url: directOgImageUrl,
              width: 1200,
              height: 630,
              alt: title,
            }],
          }
        : {}),
    },
    ...(directOgImageUrl
      ? {
          twitter: {
            card: "summary_large_image" as const,
            title,
            description,
            images: [directOgImageUrl],
          },
        }
      : {}),
  };
}

export default async function CompanyPageRoute({ params }: Props) {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_COMPANY_SHELL });
  const locale = await initI18nForPage(params);
  const { slug } = await params;
  cacheTag(companyCacheTag(slug));
  cacheTag(companyCsvDataCacheTag());

  // Prerender the unauthenticated, no-filter ``CompanyPageData`` and
  // embed it as ``initialData`` so anonymous visitors hit a CDN-cached
  // shell with zero client-side server-action round-trips (#3203,
  // mirrors `/explore` from #2640). ``fetchCompanyPageDefaults``
  // deliberately avoids ``headers()``/``cookies()`` to stay
  // ISR-eligible — the client component reuses app-bootstrap preferences and
  // resolves personalized/filter-bearing results browser-direct through the
  // scoped Typesense key when required.
  const initialData = await getCompanyRouteSnapshot(slug, locale);
  if (!initialData) notFound();
  const { company } = initialData;

  // The page body is `'use cache'`-wrapped (1-day revalidate) so the
  // anonymous static shell ships from the per-region cache without
  // invoking a function on every request. Anything that reads
  // `CompanyPage` refreshes anonymous postings directly from Typesense after
  // hydration, preserving visible freshness. `searchParams`, `headers()`,
  // `cookies()`, or session state inside
  // this function would either fail the build or kill the cache. The
  // back-link (filter-aware) and similar-companies strip live in client
  // subtrees that read `useSearchParams()` so the shell here stays
  // cache-friendly. See issue #2243.
  return (
    <div className="space-y-4">
      <CompanyHead company={company} locale={locale} />
      <Suspense fallback={null}>
        <SimilarSection
          companyId={company.id}
          industryId={company.industryId}
          initialPage={initialData.similarCompanies}
          locale={locale}
        />
      </Suspense>
      <Suspense fallback={<CompanySkeleton />}>
        <CompanyContent locale={locale} slug={slug} initialData={initialData} />
      </Suspense>
    </div>
  );
}
