import type { Metadata } from "next";
import { Suspense } from "react";
import { cacheLife, cacheTag } from "next/cache";
import {
  isLocale,
  defaultLocale,
  loadCatalog,
  ogLocale,
  ogAlternateLocales,
  type Locale,
} from "@/lib/i18n";
import { watchlistCacheTag } from "@/lib/cache-tags";
import { CACHE_TTL_LONG, CACHE_TTL_SHORT } from "@/lib/cache-ttl";
import {
  getPublicWatchlistByUserAndSlug,
  getWatchlistMatchingCompanyCount,
} from "@/lib/actions/watchlists";
import { isQualifyingWatchlist } from "@/lib/watchlist-utils";
import { siteConfig } from "@/content/config";
import { buildAlternates, buildWatchlistItemListJsonLd, JsonLd } from "@/lib/seo";
import {
  fetchPublicWatchlistPageData,
  fetchWatchlistPageData,
} from "@/lib/actions/watchlist-page-data";
import { WatchlistContent } from "./watchlist-content";
import { WatchlistRuntimeFallback } from "./watchlist-runtime-fallback";

// Metadata is cached for an hour; the page body uses the short tier so
// its posting list stays reasonably fresh. Each is its own `'use cache'`
// boundary — under cacheComponents they run in
// separate clean AsyncLocalStorage snapshots, so React's `cache()`
// wouldn't dedupe the watchlist lookup across them. Cross-boundary
// dedup lives one level down: `getPublicWatchlistByUserAndSlug` is
// wrapped in Redis `cached()` (60s TTL) so the two callers share a
// single SQL roundtrip. Watchlist metadata is shared via the CDN, so
// the anonymous page body is also rendered at this boundary, while
// viewers with personalization hints refresh it client-side. Search
// engines re-crawl on a much slower cadence than an hour anyway. See
// issues #2648 and #5980.

type Props = {
  params: Promise<{ lang: string; userSlug: string; watchlistSlug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_LONG });
  const { userSlug, watchlistSlug, lang } = await params;
  cacheTag(watchlistCacheTag(userSlug, watchlistSlug));
  const locale = isLocale(lang) ? lang : defaultLocale;
  const [detail, { i18n }] = await Promise.all([
    // Public-only fetch: never reads session, so the page stays
    // statically prerenderable. The session-aware variant is only used
    // when viewer-specific data is needed, either in the runtime
    // missing/private boundary or by the public page's client refresh.
    // See issue #2244. The Redis `cached()` wrapper inside
    // `getPublicWatchlistByUserAndSlug` shares this lookup with the
    // page-render call below.
    getPublicWatchlistByUserAndSlug(userSlug, watchlistSlug),
    loadCatalog(locale),
  ]);
  // No detail = watchlist doesn't exist or is private. Returning bare
  // `{}` would let `[lang]/layout.tsx`'s `metadata.title.default`
  // ("Job Seek") cascade and leave the page indexable. Tag explicitly
  // as `noindex,follow` so search engines don't surface ghost
  // watchlist URLs.
  if (!detail) return { robots: { index: false, follow: true } };

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
          comment: "SEO metadata suffix when a watchlist includes more companies than can be listed by name.",
          message: "{count} more",
          values: { count: detail.companies.length - 3 },
        }));
      }
      parts.push(i18n._({
        id: "watchlist.meta.jobsAt",
        comment: "SEO metadata phrase listing companies tracked by a public watchlist.",
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
        comment: "SEO metadata phrase listing locations filtered by a public watchlist.",
        message: "in {locations}",
        values: { locations: detail.filters.locationSlugs.slice(0, 2).map((s) => s.replace(/-/g, " ")).join(", ") },
      }));
    }
    description = parts.length > 0
      ? parts.join(" · ")
      : i18n._({
          id: "watchlist.meta.fallback",
          comment: "Fallback SEO description for a public watchlist with no description or filters.",
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
      comment: "SEO description prefix for a public watchlist; {description} is the existing watchlist summary.",
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
    // No `images` override — the per-watchlist `opengraph-image.tsx`
    // sibling generates richer cards (title + owner + company count +
    // filter count). Setting `images` here would bypass it.
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}${path}`,
      type: "website",
      locale: ogLocale(locale),
      alternateLocale: ogAlternateLocales(locale),
    },
    ...(!indexable && { robots: { index: false, follow: true } }),
  };
}

async function getWatchlistRouteSnapshot(
  locale: Locale,
  userSlug: string,
  watchlistSlug: string,
) {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_SHORT });
  cacheTag(watchlistCacheTag(userSlug, watchlistSlug));

  // Re-fetch the watchlist detail. The Redis `cached()` layer inside
  // `getPublicWatchlistByUserAndSlug` shares the result with the
  // `generateMetadata` call above (single SQL per cold cache fill).
  // The body and its structured data must reach non-JS consumers (AI
  // retrievers and search-engine crawlers that don't execute JS), so
  // render the anonymous snapshot here. Viewers with personalization
  // hints replace it through WatchlistContent's server action.
  const detail = await getPublicWatchlistByUserAndSlug(userSlug, watchlistSlug);
  if (!detail) return null;

  const initialData = await fetchPublicWatchlistPageData({ detail, locale });

  // For `anyCompany` watchlists, `detail.companies` is unrelated to
  // what the watchlist actually tracks (it holds leftover rows from
  // source copies — see existing comment in `generateMetadata`).
  // Emitting `Organization` references for that noise would mislead
  // schema consumers. Skip JSON-LD entirely for those; the page
  // metadata description still describes the watchlist correctly.
  const itemListJsonLd = !detail.filters.anyCompany
    ? buildWatchlistItemListJsonLd(
        { title: detail.title, companies: detail.companies },
        locale,
      )
    : null;

  return { initialData, itemListJsonLd };
}

async function WatchlistRuntimeContent({
  locale,
  userSlug,
  watchlistSlug,
}: {
  locale: Locale;
  userSlug: string;
  watchlistSlug: string;
}) {
  const initialData = await fetchWatchlistPageData({
    userSlug,
    watchlistSlug,
    locale,
  });

  return (
    <WatchlistContent
      lang={locale}
      userSlug={userSlug}
      watchlistSlug={watchlistSlug}
      initialData={initialData}
      viewerResolved
    />
  );
}

export default async function WatchlistRoute({ params }: Props) {
  const { lang, userSlug, watchlistSlug } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const snapshot = await getWatchlistRouteSnapshot(
    locale,
    userSlug,
    watchlistSlug,
  );

  if (!snapshot) {
    return (
      <Suspense fallback={<WatchlistRuntimeFallback />}>
        <WatchlistRuntimeContent
          locale={locale}
          userSlug={userSlug}
          watchlistSlug={watchlistSlug}
        />
      </Suspense>
    );
  }

  return (
    <>
      {snapshot.itemListJsonLd && <JsonLd data={snapshot.itemListJsonLd} />}
      <WatchlistContent
        lang={locale}
        userSlug={userSlug}
        watchlistSlug={watchlistSlug}
        initialData={snapshot.initialData}
      />
    </>
  );
}
