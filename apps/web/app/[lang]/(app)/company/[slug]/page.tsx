import type { Metadata } from "next";
import { cacheLife } from "next/cache";
import { isLocale, defaultLocale, loadCatalog, initI18nForPage, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { getCompanyBySlug } from "@/lib/actions/company";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { CompanyHead } from "./company-head";
import { CompanyContent } from "./company-content";
import { SimilarSection } from "./similar-section";

type Props = {
  params: Promise<{ lang: string; slug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: 600 });
  const { slug, lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const [company, { i18n }] = await Promise.all([
    getCompanyBySlug(slug, locale),
    loadCatalog(locale),
  ]);
  if (!company) return {};

  const title = i18n._({
    id: "company.meta.title",
    message: "Jobs at {name}",
    values: { name: company.name },
  });
  const count = company.activeJobCount;
  const countText = count > 0
    ? i18n._({
        id: "company.meta.positionCount",
        message: "{count, plural, one {# open position} other {# open positions}}",
        values: { count },
      })
    : i18n._({ id: "company.meta.openPositions", message: "Open positions" });
  const description = company.description
    ? i18n._({
        id: "company.meta.descriptionWithInfo",
        message: "{countText} at {name}. {description}",
        values: { countText, name: company.name, description: company.description },
      })
    : i18n._({
        id: "company.meta.descriptionBasic",
        message: "{countText} at {name}",
        values: { countText, name: company.name },
      });
  const path = `/company/${slug}`;

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
    // No `images` override here — the per-company `opengraph-image.tsx`
    // sibling generates richer OG cards (logo + name + description + meta
    // chips) that should win. Setting `images` at the page level would
    // bypass the file-convention auto-discovery.
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}${path}`,
      type: "website",
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
    },
  };
}

async function CompanyNotFound() {
  const { i18n } = await loadCatalog(defaultLocale);
  const title = i18n._({
    id: "company.notFound.title",
    comment: "Heading shown when a company page slug does not exist",
    message: "Company not found",
  });
  const message = i18n._({
    id: "company.notFound.message",
    comment: "Body text shown when a company page slug does not exist",
    message: "The company you are looking for does not exist or has been removed.",
  });
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="mt-2 text-muted">{message}</p>
    </div>
  );
}

export default async function CompanyPageRoute({ params }: Props) {
  "use cache";
  cacheLife({ revalidate: 600 });
  const locale = await initI18nForPage(params);
  const { slug } = await params;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) return <CompanyNotFound />;

  // The page body is `'use cache'`-wrapped (10-minute revalidate) so the
  // anonymous static shell ships from the per-region cache without
  // invoking a function on every request. Anything that reads
  // `searchParams`, `headers()`, `cookies()`, or session state inside
  // this function would either fail the build or kill the cache. The
  // back-link (filter-aware) and similar-companies strip live in client
  // subtrees that read `useSearchParams()` so the shell here stays
  // cache-friendly. See issue #2243.
  return (
    <div className="space-y-4">
      <CompanyHead company={company} locale={locale} />
      <SimilarSection
        companyId={company.id}
        industryId={company.industryId}
        locale={locale}
      />
      <CompanyContent locale={locale} slug={slug} />
    </div>
  );
}
