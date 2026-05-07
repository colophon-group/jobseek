import type { Metadata } from "next";
import { cache } from "react";
import { cacheLife } from "next/cache";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import {
  getPublicWatchlistByUserAndSlug,
  getWatchlistMatchingCompanyCount,
} from "@/lib/actions/watchlists";
import { isQualifyingWatchlist } from "@/lib/watchlist-utils";
import { siteConfig } from "@/content/config";
import { buildAlternates, buildWatchlistItemListJsonLd, JsonLd } from "@/lib/seo";
import { WatchlistContent } from "./watchlist-content";

/**
 * Per-request memoization so the watchlist detail is fetched once even
 * though both `generateMetadata` (above) and the page render (below)
 * need it. The outer `'use cache'` adds cross-request caching; this
 * inner React-cache wrapper still saves a duplicate SQL call within the
 * single request that populates the cache.
 */
const cachedDetail = cache(getPublicWatchlistByUserAndSlug);

// 1-hour revalidate. Watchlist metadata is shared via the CDN, so
// freshness from a viewer's perspective comes from the client-hydrated
// body. Search engines re-crawl on a much slower cadence than 10
// minutes anyway. See issue #2648.

type Props = {
  params: Promise<{ lang: string; userSlug: string; watchlistSlug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: 3600 });
  const { userSlug, watchlistSlug, lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const [detail, { i18n }] = await Promise.all([
    // Public-only fetch: never reads session, so the page stays
    // statically prerenderable. The session-aware variant is only used
    // by the client-fired server action that hydrates the page body.
    // See issue #2244. `cachedDetail` is the React-cache-wrapped form
    // so this call shares its result with the page-render call below.
    cachedDetail(userSlug, watchlistSlug),
    loadCatalog(locale),
  ]);
  if (!detail) return {};

  const ownerLabel = detail.owner.displayUsername ?? detail.owner.username ?? detail.owner.name;
  const title = `${detail.title} — @${ownerLabel}`;

  let description = detail.description;
  if (!description) {
    const parts: string[] = [];
    if (detail.companies.length > 0) {
      const names = detail.companies.slice(0, 3).map((c) => c.name);
      if (detail.companies.length > 3) {
        names.push(i18n._({
          id: "watchlist.meta.moreCompanies",
          message: "{count} more",
          values: { count: detail.companies.length - 3 },
        }));
      }
      parts.push(i18n._({
        id: "watchlist.meta.jobsAt",
        message: "Jobs at {names}",
        values: { names: names.join(", ") },
      }));
    }
    if (detail.filters.occupationSlugs?.length) {
      parts.push(detail.filters.occupationSlugs.map((s) => s.replace(/-/g, " ")).join(", "));
    }
    if (detail.filters.locationSlugs?.length) {
      parts.push(i18n._({
        id: "watchlist.meta.inLocations",
        message: "in {locations}",
        values: { locations: detail.filters.locationSlugs.slice(0, 2).map((s) => s.replace(/-/g, " ")).join(", ") },
      }));
    }
    description = parts.length > 0
      ? parts.join(" · ")
      : i18n._({
          id: "watchlist.meta.fallback",
          message: "Job watchlist by @{owner}",
          values: { owner: ownerLabel },
        });
  }
  // For `anyCompany` watchlists, `detail.companies` is unrelated to what
  // the watchlist actually tracks (it holds leftover rows from source
  // copies). Ask Typesense how many distinct companies currently have
  // postings matching the filter. Languages are intentionally NOT scoped
  // to the viewer here — metadata is shared across all viewers via the
  // CDN cache (ISR), so we use the broadest count (all languages). The
  // page body re-runs the count scoped to the viewer's language
  // preference once it hydrates.
  const companyCount = detail.filters.anyCompany
    ? await getWatchlistMatchingCompanyCount(detail.filters)
    : detail.companies.length;
  if (companyCount > 0) {
    description = i18n._({
      id: "watchlist.meta.tracking",
      message: "{count, plural, one {Tracking # company} other {Tracking # companies}}. {description}",
      values: { count: companyCount, description },
    });
  }

  const path = `/${userSlug}/${watchlistSlug}`;
  // Mirror the sitemap quality gate (#2823): if the watchlist wouldn't
  // be in the sitemap, also `noindex,follow` it on direct discovery
  // (someone shares the link, Google crawls it). Predicate lives in
  // `watchlist-utils.ts::isQualifyingWatchlist` so SQL and JS stay in
  // lockstep.
  const indexable = isQualifyingWatchlist({
    title: detail.title,
    filters: detail.filters,
    companyCount: detail.companies.length,
    createdAt: detail.createdAt,
  });
  return {
    title,
    description,
    alternates: buildAlternates(path, locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}${path}`,
      type: "website",
    },
    ...(!indexable && { robots: { index: false, follow: true } }),
  };
}

export default async function WatchlistRoute({ params }: Props) {
  "use cache";
  cacheLife({ revalidate: 3600 });
  const { lang, userSlug, watchlistSlug } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;

  // Re-fetch via the React-cache-wrapped form so this call shares its
  // result with `generateMetadata` above (single SQL per ISR regen).
  // The body is client-rendered via WatchlistContent, but the
  // structured data must reach non-JS consumers (AI retrievers,
  // search-engine crawlers that don't execute JS) — emitting it
  // server-side is the only path.
  const detail = await cachedDetail(userSlug, watchlistSlug);

  // For `anyCompany` watchlists, `detail.companies` is unrelated to
  // what the watchlist actually tracks (it holds leftover rows from
  // source copies — see existing comment in `generateMetadata`).
  // Emitting `Organization` references for that noise would mislead
  // schema consumers. Skip JSON-LD entirely for those; the page
  // metadata description still describes the watchlist correctly.
  const itemListJsonLd = detail && !detail.filters.anyCompany
    ? buildWatchlistItemListJsonLd(
        { title: detail.title, companies: detail.companies },
        locale,
      )
    : null;

  return (
    <>
      {itemListJsonLd && <JsonLd data={itemListJsonLd} />}
      <WatchlistContent lang={lang} userSlug={userSlug} watchlistSlug={watchlistSlug} />
    </>
  );
}
