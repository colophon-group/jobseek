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
  // If company count exceeds ~500, consider splitting with generateSitemaps().
  const companies = await db.execute<{
    slug: string;
    updated_at: Date;
  }>(sql`
    SELECT c.slug, c.updated_at
    FROM company c
    WHERE EXISTS (
      SELECT 1 FROM job_posting jp
      WHERE jp.company_id = c.id AND jp.is_active = true
    )
    ORDER BY c.slug
  `);

  for (const row of companies) {
    const path = `/company/${row.slug}`;
    const languages = langAlternates(path);
    for (const locale of locales) {
      entries.push({
        url: `${siteConfig.url}/${locale}${path}`,
        lastModified: row.updated_at,
        changeFrequency: "daily",
        priority: 0.8,
        alternates: { languages },
      });
    }
  }

  return entries;
}
