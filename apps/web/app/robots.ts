import type { MetadataRoute } from "next";
import { siteConfig } from "@/content/config";
import { locales } from "@/lib/i18n";
import { planSitemapShards } from "@/lib/sitemap";

// Match the sitemap-index handler's CDN window so robots.txt and the
// sitemap stay in lockstep when shards are added or removed.
export const revalidate = 3600;

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

export default async function robots(): Promise<MetadataRoute.Robots> {
  const disallow = expandDisallow(siteConfig.seo.disallow as unknown as string[]);

  // Belt-and-braces: declare both the sitemap *index* and every
  // *shard* directly. Standards-compliant crawlers follow the index
  // entries on their own, but listing shards explicitly here gives
  // long-tail bots that don't recurse into <sitemapindex> a direct
  // path to the URLs. planSitemapShards already catches its own
  // upstream errors and falls back to [{id:0}], so this never throws.
  let shardUrls: string[] = [];
  try {
    const shards = await planSitemapShards();
    shardUrls = shards.map(({ id }) => `${siteConfig.url}/sitemap/${id}.xml`);
  } catch {
    // Defense-in-depth: if a future change makes the planner throw,
    // robots.txt still ships with at least the index entry.
  }

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
    sitemap: [`${siteConfig.url}/sitemap.xml`, ...shardUrls],
  };
}
