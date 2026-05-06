import type { MetadataRoute } from "next";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

/**
 * Sitemap data + entry builders, shared by `app/sitemap.xml/route.ts`
 * (the index) and `app/sitemap/[id]/route.ts` (the shards).
 *
 * Why a shared module instead of `app/sitemap.ts`: with Next.js 16's
 * file convention, exporting `generateSitemaps` from `app/sitemap.ts`
 * registers a metadata route at `/sitemap.xml` that conflicts with an
 * explicit Route Handler at the same URL. We need the explicit handler
 * because the file convention's auto-emitted index never reaches
 * production crawlers (issue #2694) — so we drop the file convention
 * entirely and own routing for both the index and the shards.
 */

/**
 * Companies emitted per shard. With 4 locales each company yields 4
 * URLs, so 200 companies/shard ≈ 800 URLs/shard — comfortably under
 * the 50,000-URL / 50 MB sitemap.org limits and small enough to render
 * in a couple of hundred milliseconds. Crossing this threshold creates
 * a new shard rather than growing the single XML payload.
 *
 * The previous monolithic sitemap rendered ~9 MB at 1500+ companies;
 * per issue #2646 we shard so each crawler fetch loads only the slice
 * it needs and per-shard ISR regeneration is bounded.
 */
export const COMPANIES_PER_SHARD = 200;

/** ISR window for cached company / watchlist data. */
export const SITEMAP_TTL_SECONDS = 3600;

const CURATED_USERNAME = "colophongroup";

export type SitemapCompanyRow = {
  slug: string;
  updated_at: Date;
  active_count: number;
};

export type SitemapWatchlistRow = {
  user_slug: string;
  watchlist_slug: string;
  updated_at: Date;
  is_curated: boolean;
};

/**
 * Fetch all sitemap-eligible companies, paginated through Typesense.
 * The shard router slices the returned array — it's still cheaper to
 * pull the full ordered list once per Redis-cache window than to
 * invoke Typesense N times per shard regen.
 */
async function fetchSitemapCompanies(): Promise<SitemapCompanyRow[]> {
  // Use Typesense company collection (has precomputed
  // active_posting_count) instead of correlated subquery on Supabase
  // that was consuming 10% compute.
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
    // Typesense unavailable — fall back to simple Postgres query
    // without counts.
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
  // Quality gate (#2823). Without it, every public watchlist enters the
  // sitemap regardless of substance — empirical inspection (~half of
  // public watchlists are templated / default-titled / thin) shows
  // that as the surface scales the templated ones become a doorway-
  // page signal. Filter at emit time:
  //   - title is substantive (≥4 chars, not the default "New watchlist")
  //   - watchlist is at least 7 days old (lets the user populate it)
  //   - tracks ≥3 companies OR carries ≥1 keyword OR ≥2 taxonomy filters.
  //     Salary/experience-only watchlists don't qualify on their own —
  //     too thin to be a useful landing page.
  return db.execute<SitemapWatchlistRow>(sql`
    SELECT
      COALESCE(u.display_username, u.username) AS user_slug,
      w.slug AS watchlist_slug,
      w.updated_at,
      (u.username = ${CURATED_USERNAME}) AS is_curated
    FROM watchlist w
    JOIN "user" u ON u.id = w.user_id
    LEFT JOIN watchlist_company wc ON wc.watchlist_id = w.id
    WHERE w.is_public = true
      AND u.username IS NOT NULL
      AND w.title IS NOT NULL
      AND LENGTH(TRIM(w.title)) >= 4
      AND LOWER(TRIM(w.title)) <> 'new watchlist'
      AND w.created_at < NOW() - INTERVAL '7 days'
    GROUP BY w.id, u.username, u.display_username
    HAVING
      COUNT(wc.company_id) >= 3
      OR COALESCE(jsonb_array_length(w.filters->'keywords'), 0) > 0
      OR COALESCE(jsonb_array_length(w.filters->'locationSlugs'), 0)
         + COALESCE(jsonb_array_length(w.filters->'occupationSlugs'), 0)
         + COALESCE(jsonb_array_length(w.filters->'senioritySlugs'), 0)
         + COALESCE(jsonb_array_length(w.filters->'technologySlugs'), 0) >= 2
    ORDER BY is_curated DESC, w.updated_at DESC
  `) as unknown as Promise<SitemapWatchlistRow[]>;
}

export const cachedSitemapCompanies = () =>
  cached("sitemap:companies", fetchSitemapCompanies, {
    ttl: SITEMAP_TTL_SECONDS,
    // If the fetcher returns zero rows it almost always means a
    // transient outage (Typesense alias swap, Postgres timeout) — not
    // a legitimate "we have no companies". Skip caching so the next
    // request gets another chance instead of poisoning the outer ISR
    // window with an empty list. See #2245.
    skipIf: (rows) => rows.length === 0,
  });

export const cachedSitemapWatchlists = () =>
  cached("sitemap:watchlists", fetchSitemapWatchlists, {
    ttl: SITEMAP_TTL_SECONDS,
  });

/**
 * Build hreflang alternates map for a given path (without locale prefix).
 *
 * Includes `x-default` pointing at the English variant — matching the
 * convention in `buildAlternates` (`@/lib/seo`) so page metadata and
 * sitemap hreflang stay consistent (#2825). Without `x-default`, Bing
 * raises an ambiguity warning and Google's "alternate page with
 * proper canonical tag" classification gets noisier.
 */
function langAlternates(path: string): Record<string, string> {
  const languages: Record<string, string> = {};
  for (const locale of locales) {
    languages[locale] = `${siteConfig.url}/${locale}${path}`;
  }
  languages["x-default"] = `${siteConfig.url}/en${path}`;
  return languages;
}

export function staticAndExploreEntries(): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];

  for (const item of siteConfig.seo.sitemap) {
    const suffix = item.path === "/" ? "" : item.path;
    const languages = langAlternates(suffix);
    // `lastModified` reflects the last time the page's bot-visible
    // content changed. `new Date()` (the previous behavior) makes
    // every regen claim "modified now" and Bing eventually discounts
    // the signal (#2824). Real content changes should bump the
    // per-entry value in `siteConfig.seo.sitemap`.
    const lastModified = new Date(item.lastModified);
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${suffix}`,
        lastModified,
        changeFrequency: item.changeFrequency as "weekly" | "monthly",
        priority: item.priority,
        alternates: { languages },
      });
    }
  }

  const exploreLanguages = langAlternates("/explore");
  // `/explore` hosts Typesense-backed search results that change
  // continuously, but the bot-visible HTML shell (filters, top-level
  // copy) only changes on deploy. A stable date pinned to recent
  // explore-page deploys is a more honest signal than `new Date()`.
  // Bump when the prerendered shell substantively changes.
  const exploreLastModified = new Date(siteConfig.seo.exploreLastModified);
  for (const locale of locales) {
    entries.push({
      url: `${siteConfig.url}/${locale}/explore`,
      lastModified: exploreLastModified,
      changeFrequency: "daily",
      priority: 0.9,
      alternates: { languages: exploreLanguages },
    });
  }

  return entries;
}

export function watchlistEntries(rows: SitemapWatchlistRow[]): MetadataRoute.Sitemap {
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

export function companyEntries(rows: SitemapCompanyRow[]): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];
  for (const row of rows) {
    const path = `/company/${row.slug}`;
    const languages = langAlternates(path);
    // Scale priority: 0.9 for 20+ postings, 0.8 for 5+, 0.7 otherwise.
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
 * Plan the shards.
 *
 *   shard 0 — static pages + /explore + all public watchlists
 *   shard 1..N — companies, COMPANIES_PER_SHARD per shard
 *
 * On a Typesense + Postgres dual outage, falls back to "shard 0
 * only" so the static URLs (which need no DB) still reach crawlers.
 */
export async function planSitemapShards(): Promise<{ id: number }[]> {
  let companies: SitemapCompanyRow[] = [];
  try {
    companies = await cachedSitemapCompanies();
  } catch (err) {
    console.error(
      "[sitemap] planSitemapShards: company fetch failed; emitting only shard 0",
      err,
    );
    return [{ id: 0 }];
  }
  const companyShards = Math.max(
    1,
    Math.ceil(companies.length / COMPANIES_PER_SHARD),
  );
  // Shard 0 is reserved for static + watchlists, so total = companyShards + 1.
  return Array.from({ length: companyShards + 1 }, (_, i) => ({ id: i }));
}

/**
 * Render a single shard. Each fetcher is wrapped in try/catch so a
 * Postgres or Typesense outage degrades to a partial sitemap rather
 * than an empty `<urlset/>`. Empty shards were the second half of
 * issue #2694: a thrown fetcher tore the whole response down.
 */
export async function renderSitemapShard(id: number): Promise<MetadataRoute.Sitemap> {
  if (id === 0) {
    let watchlists: SitemapWatchlistRow[] = [];
    try {
      watchlists = await cachedSitemapWatchlists();
    } catch (err) {
      console.error(
        "[sitemap] watchlist fetch failed; serving static entries only",
        err,
      );
    }
    return [...staticAndExploreEntries(), ...watchlistEntries(watchlists)];
  }

  let companies: SitemapCompanyRow[] = [];
  try {
    companies = await cachedSitemapCompanies();
  } catch (err) {
    console.error(`[sitemap] company fetch failed for shard ${id}`, err);
    return [];
  }
  const start = (id - 1) * COMPANIES_PER_SHARD;
  const slice = companies.slice(start, start + COMPANIES_PER_SHARD);
  return companyEntries(slice);
}

/**
 * Serialize a `MetadataRoute.Sitemap` shape (the same JS shape Next's
 * file-convention sitemap emits) to a `<urlset>` XML string.
 */
export function serializeUrlset(entries: MetadataRoute.Sitemap): string {
  const lines: string[] = [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">`,
  ];
  for (const entry of entries) {
    lines.push(`  <url>`);
    lines.push(`    <loc>${escapeXml(entry.url)}</loc>`);
    if (entry.lastModified) {
      const d = entry.lastModified instanceof Date
        ? entry.lastModified
        : new Date(entry.lastModified);
      lines.push(`    <lastmod>${d.toISOString()}</lastmod>`);
    }
    if (entry.changeFrequency) {
      lines.push(`    <changefreq>${entry.changeFrequency}</changefreq>`);
    }
    if (typeof entry.priority === "number") {
      lines.push(`    <priority>${entry.priority.toFixed(1)}</priority>`);
    }
    const langs = entry.alternates?.languages;
    if (langs) {
      for (const [hreflang, href] of Object.entries(langs)) {
        if (typeof href === "string") {
          lines.push(
            `    <xhtml:link rel="alternate" hreflang="${escapeXml(hreflang)}" href="${escapeXml(href)}"/>`,
          );
        }
      }
    }
    lines.push(`  </url>`);
  }
  lines.push(`</urlset>`, "");
  return lines.join("\n");
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
