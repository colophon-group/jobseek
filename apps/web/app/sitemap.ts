import type { MetadataRoute } from "next";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

/** Build hreflang alternates map for a given path (without locale prefix). */
function langAlternates(path: string): Record<string, string> {
  const languages: Record<string, string> = {};
  for (const locale of locales) {
    languages[locale] = `${siteConfig.url}/${locale}${path}`;
  }
  return languages;
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];

  // ── Static pages ───────────────────────────────────────────────────
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

  // ── Explore page ───────────────────────────────────────────────────
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

  // ── Company pages ──────────────────────────────────────────────────
  // Only include companies that have at least one active job posting.
  // Ordered by active posting count so high-content pages are crawled first.
  // If company count exceeds ~500, consider splitting with generateSitemaps().
  const companies = await db.execute<{
    slug: string;
    updated_at: Date;
    active_count: number;
  }>(sql`
    SELECT c.slug, c.updated_at,
      (SELECT count(*) FROM job_posting jp
       WHERE jp.company_id = c.id AND jp.is_active = true
      )::int AS active_count
    FROM company c
    WHERE EXISTS (
      SELECT 1 FROM job_posting jp
      WHERE jp.company_id = c.id AND jp.is_active = true
    )
    ORDER BY active_count DESC, c.slug
  `);

  for (const row of companies) {
    const path = `/company/${row.slug}`;
    const languages = langAlternates(path);
    // Scale priority: 0.9 for 20+ postings, 0.8 for 5+, 0.7 otherwise
    const priority = row.active_count >= 20 ? 0.9 : row.active_count >= 5 ? 0.8 : 0.7;
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${path}`,
        lastModified: row.updated_at,
        changeFrequency: "daily",
        priority,
        alternates: { languages },
      });
    }
  }

  // ── Public watchlists ─────────────────────────────────────────────
  // Curated (colophongroup) watchlists get higher priority than user-created ones.
  const CURATED_USERNAME = "colophongroup";
  const watchlists = await db.execute<{
    user_slug: string;
    watchlist_slug: string;
    updated_at: Date;
    is_curated: boolean;
  }>(sql`
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
  `);

  for (const row of watchlists) {
    const path = `/${row.user_slug}/${row.watchlist_slug}`;
    const languages = langAlternates(path);
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${path}`,
        lastModified: row.updated_at,
        changeFrequency: row.is_curated ? "daily" : "weekly",
        priority: row.is_curated ? 0.8 : 0.6,
        alternates: { languages },
      });
    }
  }

  return entries;
}
