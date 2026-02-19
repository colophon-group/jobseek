import type { MetadataRoute } from "next";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

export default function sitemap(): MetadataRoute.Sitemap {
  const entries: MetadataRoute.Sitemap = [];

  for (const item of siteConfig.seo.sitemap) {
    for (const locale of locales) {
      const suffix = item.path === "/" ? "" : item.path;
      entries.push({
        url: `${siteConfig.url}/${locale}${suffix}`,
        lastModified: new Date(),
        changeFrequency: item.changeFrequency as "weekly" | "monthly",
        priority: item.priority,
      });
    }
  }

  return entries;
}
