import type { MetadataRoute } from "next";
import { siteConfig } from "@/content/config";

export default function robots(): MetadataRoute.Robots {
  const disallow = siteConfig.seo.disallow as unknown as string[];

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
