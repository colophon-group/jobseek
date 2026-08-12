import snapshot from "@/content/blog/mention-snapshot.json";
import { defaultLocale, isLocale, type Locale } from "@/lib/i18n";

/**
 * Repository-owned data used by MDX entity mentions.
 *
 * This module is deliberately synchronous and side-effect free. In particular,
 * it must never grow a database, Typesense, Redis, HTTP, or `fetch` fallback:
 * blog pages are prerendered for four locales and their build-time external-call
 * budget is zero. `pnpm blog-mentions:check` validates both the snapshot and this
 * import boundary before every production build.
 */

type CompanySnapshotEntry = (typeof snapshot.companies)[number];
type WatchlistSnapshotEntry = (typeof snapshot.watchlists)[number];

export type BlogCompanyMention = {
  slug: string;
  name: string;
  icon: string | null;
  description: string | null;
  industryName: string | null;
  employeeCountRange: number | null;
  foundedYear: number | null;
  /** Volatile posting counts are intentionally absent from the snapshot. */
  activeJobCount: null;
};

export type BlogWatchlistMention = {
  owner: string;
  ownerLabel: string;
  slug: string;
  title: string;
  description: string | null;
  /** Optional only when an author approves a point-in-time editorial value. */
  companyCount: number | null;
};

const companiesBySlug = new Map(
  snapshot.companies.map((company) => [company.slug, company] as const),
);
const watchlistsByKey = new Map(
  snapshot.watchlists.map((watchlist) => [
    `${watchlist.owner}/${watchlist.slug}`,
    watchlist,
  ] as const),
);

function normalizedLocale(locale: string): Locale {
  return isLocale(locale) ? locale : defaultLocale;
}

function localizedDescription(
  company: CompanySnapshotEntry,
  locale: Locale,
): string | null {
  return company.descriptions[locale] ?? company.descriptions.en ?? null;
}

export function resolveBlogCompanyMention(
  slug: string,
  locale: string,
): BlogCompanyMention | null {
  const company = companiesBySlug.get(slug);
  if (!company) return null;

  return {
    slug: company.slug,
    name: company.name,
    icon: company.icon,
    description: localizedDescription(company, normalizedLocale(locale)),
    industryName: company.industryName,
    employeeCountRange: company.employeeCountRange,
    foundedYear: company.foundedYear,
    activeJobCount: null,
  };
}

export function resolveBlogWatchlistMention(
  owner: string,
  slug: string,
): BlogWatchlistMention | null {
  const watchlist: WatchlistSnapshotEntry | undefined = watchlistsByKey.get(
    `${owner}/${slug}`,
  );
  if (!watchlist) return null;

  return {
    owner: watchlist.owner,
    ownerLabel: watchlist.ownerLabel,
    slug: watchlist.slug,
    title: watchlist.title,
    description: watchlist.description,
    companyCount: watchlist.companyCount,
  };
}

export const BLOG_MENTION_EXTERNAL_CALL_BUDGET = 0;
