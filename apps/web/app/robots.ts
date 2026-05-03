import type { MetadataRoute } from "next";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";

// TEMPORARY: while /sitemap.xml is a monolithic <urlset> instead of a
// <sitemapindex>, robots.txt declares only that one URL — listing the
// shard URLs would have crawlers fetch overlapping content. Restore
// `planSitemapShards`-driven shard declarations when reverting the
// monolithic experiment in app/sitemap.xml/route.ts.
function expandDisallow(paths: readonly string[]): string[] {
  const out: string[] = [];
  for (const path of paths) {
    out.push(path);
    // API routes are not locale-prefixed (middleware excludes /api/*),
    // so only expand non-API paths to their locale-prefixed variants.
    if (path.startsWith("/api/")) continue;
    for (const locale of locales) {
      out.push(`/${locale}${path}`);
    }
  }
  return out;
}

export default function robots(): MetadataRoute.Robots {
  const disallow = expandDisallow(siteConfig.seo.disallow as unknown as string[]);

  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow,
      },
      // Explicitly allow AI crawlers to index public content
      {
        userAgent: ["GPTBot", "ChatGPT-User", "CCBot", "PerplexityBot", "Google-Extended", "ClaudeBot", "anthropic-ai"],
        allow: ["/", "/.well-known/llms.txt", "/.well-known/ai-plugin.json", "/api/openapi.json"],
        disallow,
      },
    ],
    sitemap: `${siteConfig.url}/sitemap.xml`,
  };
}
