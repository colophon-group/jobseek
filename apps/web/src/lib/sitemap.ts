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

/** ISR window for cached watchlist data. */
export const SITEMAP_TTL_SECONDS = 3600;

const CURATED_USERNAME = "colophongroup";

export type SitemapWatchlistRow = {
  user_slug: string;
  watchlist_slug: string;
  updated_at: Date;
  is_curated: boolean;
};

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

/**
 * Plan the shards.
 *
 * Companies are excluded from the index (#2821) — `/company/{slug}` is
 * `noindex,follow` and the per-company URLs are not emitted into any
 * shard. Only shard 0 (static + watchlists) is produced.
 *
 * The async signature is preserved for forward compatibility with the
 * route handlers and to leave room for future indexable surfaces
 * (e.g. blog) without re-shaping callers.
 */
export async function planSitemapShards(): Promise<{ id: number }[]> {
  return [{ id: 0 }];
}

/**
 * Render a single shard. Shard 0 holds static + explore + watchlists.
 * Returns `null` for any other id so the route handler can map it to
 * a 404 — a stale sitemap-index reference (from before #2821 retired
 * the company shards) gets a clear "this URL no longer exists"
 * signal rather than an empty `<urlset/>`.
 *
 * Watchlist fetcher is wrapped in try/catch so a Postgres outage
 * degrades to "static entries only" rather than an empty `<urlset/>`.
 * Empty shards were the second half of issue #2694: a thrown fetcher
 * tore the whole response down.
 */
export async function renderSitemapShard(
  id: number,
): Promise<MetadataRoute.Sitemap | null> {
  if (id !== 0) return null;
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
