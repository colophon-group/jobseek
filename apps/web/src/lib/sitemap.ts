import type { MetadataRoute } from "next";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";
import { withDbRetry } from "@/lib/db-retry";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";
import { listBlogPosts, getBlogPostLocales, type BlogPostSummary } from "@/lib/blog";

/**
 * Sitemap data + entry builders backing `app/sitemap.xml/route.ts`.
 *
 * Why a shared module instead of `app/sitemap.ts` (the Next.js file
 * convention): exporting `generateSitemaps` from `app/sitemap.ts`
 * registers a metadata route at `/sitemap.xml` that conflicts with an
 * explicit Route Handler at the same URL. The file convention's
 * auto-emitted index never reaches production crawlers (issue #2694),
 * so we drop the file convention entirely and own the route.
 *
 * The route used to be sharded (`/sitemap.xml` index + `/sitemap/<id>.xml`
 * per-shard, #2646). After companies left the index (#2821) the
 * surviving content fits in a single urlset; sharding was retired.
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
  return withDbRetry(
    () =>
      db.execute<SitemapWatchlistRow>(sql`
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
      `),
    { label: "sitemap.watchlists" },
  ) as unknown as Promise<SitemapWatchlistRow[]>;
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
 * Blog post sitemap entries (#2828). Emits one entry per post per
 * locale that has a translated MDX file on disk. Locales without a
 * translation are skipped — the post page falls back to the canonical
 * English body for those routes, but advertising a duplicate-content
 * URL via hreflang is what we want to avoid (Google would flag it as
 * an alternate-page-with-canonical cluster).
 *
 * For posts with multiple translated locales, every locale gets its
 * own URL entry plus a per-entry `languages` map listing all
 * translated siblings. The `x-default` matches `seo.tsx::buildAlternates`
 * (always points at /en/...) for consistency with other sitemap
 * entries — see #2825.
 */
export async function blogPostEntries(
  posts: BlogPostSummary[],
): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];
  for (const post of posts) {
    const postLocales = await getBlogPostLocales(post.slug);
    if (postLocales.length === 0) continue;
    const languages: Record<string, string> = {};
    for (const locale of postLocales) {
      languages[locale] = `${siteConfig.url}/${locale}/blog/${post.slug}`;
    }
    languages["x-default"] = `${siteConfig.url}/en/blog/${post.slug}`;
    for (const locale of postLocales) {
      entries.push({
        url: `${siteConfig.url}/${locale}/blog/${post.slug}`,
        lastModified: new Date(post.dateModified),
        changeFrequency: "monthly" as const,
        priority: 0.6,
        alternates: { languages },
      });
    }
  }
  return entries;
}


/**
 * Build the full sitemap entry set.
 *
 * Companies are excluded (#2821) — `/company/{slug}` is `noindex,follow`
 * and the per-company URLs are not emitted. The surviving surface is
 * static pages + explore + qualifying watchlists + blog posts.
 *
 * Each upstream fetcher is wrapped in try/catch so a Postgres outage
 * degrades to "static entries only" rather than an empty `<urlset/>`.
 * That was the second half of issue #2694 when the route was sharded:
 * a thrown fetcher tore the whole response down.
 */
export async function buildSitemap(): Promise<MetadataRoute.Sitemap> {
  let watchlists: SitemapWatchlistRow[] = [];
  try {
    watchlists = await cachedSitemapWatchlists();
  } catch (err) {
    console.error(
      "[sitemap] watchlist fetch failed; serving static entries only",
      err,
    );
  }
  let blogEntries: MetadataRoute.Sitemap = [];
  try {
    const blogPosts = await listBlogPosts();
    blogEntries = await blogPostEntries(blogPosts);
  } catch (err) {
    // Filesystem read shouldn't normally fail, but a malformed
    // frontmatter or a future change to `getBlogPostLocales` could
    // surface here. Degrade to "no blog entries" rather than tearing
    // down the whole urlset (which was the second half of #2694).
    console.error("[sitemap] blog entries failed; skipping", err);
  }
  return [
    ...staticAndExploreEntries(),
    ...watchlistEntries(watchlists),
    ...blogEntries,
  ];
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
