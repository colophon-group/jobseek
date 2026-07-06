import { buildSitemap, serializeUrlset } from "@/lib/sitemap";

/**
 * /sitemap.xml — single <urlset> for the whole site.
 *
 * The route used to be a <sitemapindex> with shard children at
 * /sitemap/<id>.xml (#2646), but after companies left the index
 * (#2821) the surviving surface (static + watchlists + blog) fits in
 * a single urlset well under the sitemap.org limits (50K URLs / 50 MB
 * uncompressed; we're at ~hundreds of URLs and Vercel auto-compresses
 * XML at the edge). Sharding was retired so the route is just one
 * builder; see `buildSitemap` in `@/lib/sitemap` for the entry set.
 *
 * Per-fetcher error handling lives inside `buildSitemap` so an outage
 * on one upstream (Postgres/blog FS) degrades gracefully without
 * tearing down the whole urlset.
 */
export async function GET(): Promise<Response> {
  const entries = await buildSitemap();
  const xml = serializeUrlset(entries);

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
