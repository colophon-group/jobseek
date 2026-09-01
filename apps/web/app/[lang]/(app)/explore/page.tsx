import type { Metadata } from "next";
import { Suspense } from "react";
import { cacheLife } from "next/cache";
import { isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales, type Locale } from "@/lib/i18n";
import { CACHE_TTL_EXPLORE_SHELL } from "@/lib/cache-ttl";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { fetchExplorePageDefaults } from "@/lib/actions/explore-page-data";
import { ExploreContent } from "./explore-content";
import { ExploreStaticResults } from "./explore-static-results";

const EXPLORE_DEFAULTS_CACHE_LIFE = {
  stale: CACHE_TTL_EXPLORE_SHELL,
  revalidate: CACHE_TTL_EXPLORE_SHELL,
  expire: CACHE_TTL_EXPLORE_SHELL * 5,
} as const;
const EXPLORE_DEFAULTS_PAYLOAD_VERSION = "v4";

// Cached for one day. The anonymous, no-filter explore payload is rendered
// server-side via `fetchExplorePageDefaults` and embedded as `initialData`.
// `SearchPage` refreshes the default result directly from Typesense after
// hydration, so the longer CDN lifetime removes background regenerations
// without making the visible job inventory stale. See #2640 + #2243.
//
// Do NOT add `searchParams` to Props or read `headers()`/`cookies()`
// here — that would force the page out of the cached path on every
// request and reintroduce the regression.

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_EXPLORE_SHELL });
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({
    id: "explore.meta.title",
    comment: "SEO title for the Explore jobs page.",
    message: "Explore Jobs",
  });
  const description = i18n._({
    id: "explore.meta.description",
    comment: "SEO description for the Explore jobs page.",
    message: "Search jobs across thousands of companies scraped directly from career pages. Filter by seniority, tech stack, salary, and location, then save searches as watchlists.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/explore", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/explore`,
      type: "website",
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
      images: [{ ...siteConfig.ogImage, alt: "Job Seek" }],
    },
  };
}

async function renderExploreContent(
  locale: Locale,
  payloadVersion: string,
) {
  "use cache";
  cacheLife(EXPLORE_DEFAULTS_CACHE_LIFE);
  if (payloadVersion !== EXPLORE_DEFAULTS_PAYLOAD_VERSION) {
    throw new Error("Unexpected explore defaults cache version");
  }
  const [initialData, { i18n }] = await Promise.all([
    fetchExplorePageDefaults({ locale }),
    loadCatalog(locale),
  ]);
  const heading = i18n._({
    id: "explore.h1",
    comment: "Hidden page H1 for /explore — screen-reader landmark",
    message: "Explore Jobs",
  });

  return (
    <>
      <ExploreStaticResults locale={locale} heading={heading} data={initialData} />
      <div data-explore-interactive hidden>
        <Suspense fallback={null}>
          <ExploreContent locale={locale} initialData={initialData} />
        </Suspense>
      </div>
    </>
  );
}

export default async function AppPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;

  return renderExploreContent(locale, EXPLORE_DEFAULTS_PAYLOAD_VERSION);
}
