import type { MetadataRoute } from "next";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

/**
 * ISR window for each generated sitemap shard.
 *
 * The default sitemap.ts behavior in Next.js 16 is "static if possible,
 * otherwise dynamic on every request". Because this handler hits Postgres
 * + Typesense at runtime (and the build step has no DB credentials), it
 * was falling all the way to fully dynamic — every Bing/Yandex/Seznam
 * crawl regenerated the full ~9 MB XML response. Setting `revalidate`
 * makes Vercel CDN-cache the rendered response for 1 hour and triggers
 * background ISR regeneration on expiry; the inner `cached()` Redis
 * wrapper stays as a defense-in-depth safety net for rare CDN evictions
 * within the window. See issue #2245.
 */
export const revalidate = 3600;

/**
 * Companies emitted per shard. With 4 locales each company yields 4 URLs,
 * so 200 companies/shard ≈ 800 URLs/shard — comfortably under the
 * 50,000-URL / 50 MB sitemap.org limits and small enough to render in a
 * couple of hundred milliseconds. Crossing this threshold creates a new
 * shard rather than growing the single XML payload.
 *
 * The previous monolithic sitemap rendered ~9 MB at 1500+ companies; per
 * issue #2646 we shard so each crawler fetch loads only the slice it
 * needs and per-shard ISR regeneration is bounded.
 */
const COMPANIES_PER_SHARD = 200;

const CURATED_USERNAME = "colophongroup";

type SitemapCompanyRow = {
  slug: string;
  updated_at: Date;
  active_count: number;
};

type SitemapWatchlistRow = {
  user_slug: string;
  watchlist_slug: string;
  updated_at: Date;
  is_curated: boolean;
};

/**
 * Fetch all sitemap-eligible companies, paginated through Typesense. The
 * shard router below slices the returned array — it's still cheaper to
 * pull the full ordered list once per Redis-cache window than to invoke
 * Typesense N times per shard regen.
 */
async function fetchSitemapCompanies(): Promise<SitemapCompanyRow[]> {
  // Use Typesense company collection (has precomputed active_posting_count)
  // instead of correlated subquery on Supabase that was consuming 10% compute.
  try {
    const { getSearchClient } = await import("@/lib/search/typesense-client");
    const client = getSearchClient();
    const perPage = 250;
    const companies: SitemapCompanyRow[] = [];

    for (let page = 1; ; page += 1) {
      const result = await client.collections("company").documents().search({
        q: "*",
        query_by: "name",
        filter_by: "active_posting_count:>0",
        sort_by: "active_posting_count:desc",
        per_page: perPage,
        page,
        include_fields: "slug,active_posting_count",
      });

      const hits = result.hits ?? [];
      for (const hit of hits) {
        const doc = hit.document as Record<string, unknown>;
        companies.push({
          slug: doc.slug as string,
          updated_at: new Date(),
          active_count: (doc.active_posting_count as number) ?? 0,
        });
      }

      if (hits.length < perPage) break;
      if (typeof result.found === "number" && page * perPage >= result.found) break;
    }

    return companies;
  } catch {
    // Typesense unavailable — fall back to simple Postgres query without counts
    const rows = await db.execute<{ slug: string; updated_at: Date }>(sql`
      SELECT c.slug, c.updated_at FROM company c
      WHERE EXISTS (SELECT 1 FROM job_posting jp WHERE jp.company_id = c.id AND jp.is_active = true)
      ORDER BY c.slug
    `);
    return (rows as unknown as { slug: string; updated_at: Date }[]).map((r) => ({
      ...r,
      active_count: 0,
    }));
  }
}

async function fetchSitemapWatchlists(): Promise<SitemapWatchlistRow[]> {
  return db.execute<SitemapWatchlistRow>(sql`
    SELECT
      COALESCE(u.display_username, u.username) AS user_slug,
      w.slug AS watchlist_slug,
      w.updated_at,
      (u.username = ${CURATED_USERNAME}) AS is_curated
    FROM watchlist w
    JOIN "user" u ON u.id = w.user_id
    WHERE w.is_public = true
      AND u.username IS NOT NULL
    ORDER BY is_curated DESC, w.updated_at DESC
  `) as unknown as Promise<SitemapWatchlistRow[]>;
}

const cachedCompanies = () =>
  cached("sitemap:companies", fetchSitemapCompanies, {
    ttl: 3600,
    // If the fetcher returns zero rows it almost always means a transient
    // outage (Typesense alias swap, Postgres timeout) — not a legitimate
    // "we have no companies". Skip caching so the next request gets
    // another chance instead of poisoning the outer ISR window. See #2245.
    skipIf: (rows) => rows.length === 0,
  });

const cachedWatchlists = () =>
  cached("sitemap:watchlists", fetchSitemapWatchlists, { ttl: 3600 });

/** Build hreflang alternates map for a given path (without locale prefix). */
function langAlternates(path: string): Record<string, string> {
  const languages: Record<string, string> = {};
  for (const locale of locales) {
    languages[locale] = `${siteConfig.url}/${locale}${path}`;
  }
  return languages;
}

function staticAndExploreEntries(): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];

  for (const item of siteConfig.seo.sitemap) {
    const suffix = item.path === "/" ? "" : item.path;
    const languages = langAlternates(suffix);
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${suffix}`,
        lastModified: new Date(),
        changeFrequency: item.changeFrequency as "weekly" | "monthly",
        priority: item.priority,
        alternates: { languages },
      });
    }
  }

  const exploreLanguages = langAlternates("/explore");
  for (const locale of locales) {
    entries.push({
      url: `${siteConfig.url}/${locale}/explore`,
      lastModified: new Date(),
      changeFrequency: "daily",
      priority: 0.9,
      alternates: { languages: exploreLanguages },
    });
  }

  return entries;
}

function watchlistEntries(rows: SitemapWatchlistRow[]): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];
  for (const row of rows) {
    const path = `/${row.user_slug}/${row.watchlist_slug}`;
    const languages = langAlternates(path);
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${path}`,
        lastModified: new Date(row.updated_at),
        changeFrequency: row.is_curated ? "daily" : "weekly",
        priority: row.is_curated ? 0.8 : 0.6,
        alternates: { languages },
      });
    }
  }
  return entries;
}

function companyEntries(rows: SitemapCompanyRow[]): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];
  for (const row of rows) {
    const path = `/company/${row.slug}`;
    const languages = langAlternates(path);
    // Scale priority: 0.9 for 20+ postings, 0.8 for 5+, 0.7 otherwise
    const priority = row.active_count >= 20 ? 0.9 : row.active_count >= 5 ? 0.8 : 0.7;
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${path}`,
        lastModified: new Date(row.updated_at),
        changeFrequency: "daily",
        priority,
        alternates: { languages },
      });
    }
  }
  return entries;
}

/**
 * Declare the sitemap shards.
 *
 *   shard 0 — static pages + /explore + all public watchlists
 *   shard 1..N — companies, COMPANIES_PER_SHARD per shard
 *
 * Next.js exposes the index at /sitemap.xml and the shards at
 * /sitemap/<id>.xml. Crawlers follow the index → shards. The shard count
 * grows with the company catalogue; one company addition only invalidates
 * its own shard's ISR window.
 */
export async function generateSitemaps(): Promise<{ id: number }[]> {
  const companies = await cachedCompanies();
  const companyShards = Math.max(
    1,
    Math.ceil(companies.length / COMPANIES_PER_SHARD),
  );
  // Shard 0 is reserved for static + watchlists, so total = companyShards + 1.
  return Array.from({ length: companyShards + 1 }, (_, i) => ({ id: i }));
}

/**
 * Render one sitemap shard. The function shape is the standard Next.js
 * sitemap default export; the `id` parameter selects which shard.
 */
export default async function sitemap({
  id,
}: { id: number } = { id: 0 }): Promise<MetadataRoute.Sitemap> {
  if (id === 0) {
    const watchlists = await cachedWatchlists();
    return [...staticAndExploreEntries(), ...watchlistEntries(watchlists)];
  }

  const companies = await cachedCompanies();
  const start = (id - 1) * COMPANIES_PER_SHARD;
  const slice = companies.slice(start, start + COMPANIES_PER_SHARD);
  return companyEntries(slice);
}
