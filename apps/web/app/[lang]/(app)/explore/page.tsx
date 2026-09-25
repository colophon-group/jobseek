import type { Metadata } from "next";
import { cacheLife } from "next/cache";
import { isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales, type Locale } from "@/lib/i18n";
import { CACHE_TTL_EXPLORE_SHELL } from "@/lib/cache-ttl";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { fetchExplorePageDefaults } from "@/lib/actions/explore-page-data";
import { SEARCH_FILTER_PARAM_KEYS } from "@/lib/search/query-params";
import { JOB_LANGUAGES_COOKIE, LOGGED_IN_COOKIE } from "@/lib/client-cookies";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { ExploreContent } from "./explore-content";

const EXPLORE_DEFAULTS_CACHE_LIFE = {
  stale: CACHE_TTL_EXPLORE_SHELL,
  revalidate: CACHE_TTL_EXPLORE_SHELL,
  expire: CACHE_TTL_EXPLORE_SHELL * 5,
} as const;
const EXPLORE_DEFAULTS_PAYLOAD_VERSION = "v5";

// Runs while the cached document is parsed, before its results are painted.
// The document remains query-agnostic and cacheable; only the browser knows
// whether its URL or viewer hint requires a different result set.
const EXPLORE_PREPAINT_SCRIPT = `(()=>{try{const params=new URLSearchParams(location.search);const keys=${JSON.stringify([...SEARCH_FILTER_PARAM_KEYS, "lang"])};const cookies=document.cookie.split(";").map(value=>value.trim());const hasCookie=name=>cookies.some(value=>value.startsWith(name+"="));if(keys.some(key=>params.has(key))||hasCookie(${JSON.stringify(LOGGED_IN_COOKIE)})||hasCookie(${JSON.stringify(JOB_LANGUAGES_COOKIE)}))document.documentElement.setAttribute("data-explore-pending","")}catch{document.documentElement.setAttribute("data-explore-pending","")}})();`;

// Cached for one day. The anonymous, no-filter explore payload is rendered
// server-side via `fetchExplorePageDefaults` and embedded as `initialData`.
// `SearchPage` checks for fresh inventory directly from Typesense after
// hydration and offers it without silently reordering the visible feed.
// See #2640 + #2243 + #10010.
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
    message: "Search jobs across thousands of companies scraped directly from career pages. Filter by seniority, tech stack, salary, and location — then save watchlists and get alerts.",
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
  const initialData = await fetchExplorePageDefaults({ locale });

  return (
    <>
      <script dangerouslySetInnerHTML={{ __html: EXPLORE_PREPAINT_SCRIPT }} />
      <div data-explore-result-host>
        <ExploreContent locale={locale} initialData={initialData} />
      </div>
      <div data-explore-pending-skeleton>
        <ExploreSkeleton />
      </div>
    </>
  );
}

export default async function AppPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;

  return renderExploreContent(locale, EXPLORE_DEFAULTS_PAYLOAD_VERSION);
}
