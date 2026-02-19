import type { MetadataRoute } from "next";
import { siteConfig } from "@/content/config";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: siteConfig.seo.disallow as unknown as string[],
    },
    sitemap: `${siteConfig.url}/sitemap.xml`,
  };
}
