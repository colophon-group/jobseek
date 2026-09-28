import snapshot from "@/content/blog/mention-snapshot.json";
import watchlistSnapshot from "@/content/blog/watchlist-mention-snapshot.json";
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

export type BlogWatchlistMention = {
  slug: string;
  name: string;
  /** Locale-prefixed canonical shared watchlist route. */
  href: string;
};

const watchlistsBySlug = new Map<string, { slug: string; name: string; path: string }>(
  (watchlistSnapshot.watchlists as { slug: string; name: string; path: string }[])
    .map((watchlist) => [watchlist.slug, watchlist] as const),
);

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

const companiesBySlug = new Map(
  snapshot.companies.map((company) => [company.slug, company] as const),
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
  slug: string,
  locale: string,
): BlogWatchlistMention | null {
  const watchlist = watchlistsBySlug.get(slug);
  if (!watchlist) return null;
  return {
    slug: watchlist.slug,
    name: watchlist.name,
    href: `/${normalizedLocale(locale)}${watchlist.path}`,
  };
}

export const BLOG_MENTION_EXTERNAL_CALL_BUDGET = 0;
