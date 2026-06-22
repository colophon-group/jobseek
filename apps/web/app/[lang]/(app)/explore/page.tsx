import type { Metadata } from "next";
import { cacheLife } from "next/cache";
import { isLocale, defaultLocale, loadCatalog, ogLocale, ogAlternateLocales } from "@/lib/i18n";
import { CACHE_TTL_SHORT } from "@/lib/cache-ttl";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { fetchExploreDefaults } from "@/lib/actions/explore-data";
import { ExploreContent } from "./explore-content";

const EXPLORE_DEFAULTS_CACHE_LIFE = {
  stale: CACHE_TTL_SHORT,
  revalidate: CACHE_TTL_SHORT,
  expire: CACHE_TTL_SHORT * 5,
} as const;

// Cached for 60s. The anonymous, no-filter explore page is rendered
// server-side via `fetchExploreDefaults` and embedded as `initialData`.
// `ExploreContent` is a client component that re-fetches a personalized
// variant only when the `logged_in` hint cookie or a filter searchParam
// is present, so anonymous no-filter visitors hit the static prerender
// without triggering a Vercel function invocation. See #2640 + #2243.
//
// Do NOT add `searchParams` to Props or read `headers()`/`cookies()`
// here — that would force the page out of the cached path on every
// request and reintroduce the regression.

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_SHORT });
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "explore.meta.title", message: "Explore Jobs" });
  const description = i18n._({
    id: "explore.meta.description",
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
      images: [{ url: "/opengraph-image", width: 1200, height: 630, alt: "Job Seek" }],
    },
  };
}

export default async function AppPage({ params }: Props) {
  "use cache";
  cacheLife(EXPLORE_DEFAULTS_CACHE_LIFE);
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const initialData = await fetchExploreDefaults({ locale });

  return <ExploreContent locale={locale} initialData={initialData} />;
}
