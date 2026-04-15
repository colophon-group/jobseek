import { cache } from "react";
import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import {
  getWatchlistByUserAndSlug as _getWatchlistByUserAndSlug,
  getWatchlistMatchingCompanyCount,
} from "@/lib/actions/watchlists";
import { isTrivialWatchlist } from "@/lib/watchlist-utils";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { WatchlistContent } from "./watchlist-content";

export const revalidate = 600; // ISR: cache metadata for 10 minutes

// Deduplicate across generateMetadata + page component within a single render
const getWatchlistByUserAndSlug = cache(_getWatchlistByUserAndSlug);

type Props = {
  params: Promise<{ lang: string; userSlug: string; watchlistSlug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { userSlug, watchlistSlug, lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const [detail, { i18n }] = await Promise.all([
    getWatchlistByUserAndSlug(userSlug, watchlistSlug),
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
  // For `anyCompany` watchlists, `detail.companies` is unrelated to what the
  // watchlist actually tracks (it holds leftover rows from source copies).
  // Ask Typesense how many distinct companies currently have postings matching
  // the filter so the social preview reflects reality.
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
  const trivial = isTrivialWatchlist(detail.filters, detail.companies.length);
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
    ...(trivial && { robots: { index: false, follow: true } }),
  };
}

export default async function WatchlistRoute({ params }: Props) {
  const { lang, userSlug, watchlistSlug } = await params;

  return <WatchlistContent lang={lang} userSlug={userSlug} watchlistSlug={watchlistSlug} />;
}
