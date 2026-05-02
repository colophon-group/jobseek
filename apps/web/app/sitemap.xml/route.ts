import { siteConfig } from "@/content/config";
import { planSitemapShards } from "@/lib/sitemap";

/**
 * Sitemap index at `/sitemap.xml`.
 *
 * Crawlers fetch this URL (advertised in `robots.txt`) and follow the
 * listed `<sitemap><loc>` entries to per-shard sitemaps. Issue #2694:
 * with Next.js's file convention + `generateSitemaps()`, this URL was
 * not being served — the request fell through to the `[lang]`
 * catch-all and returned the homepage HTML. We now own the index
 * route explicitly so the failure mode is impossible.
 *
 * The response sets explicit Cache-Control (s-maxage=3600 +
 * stale-while-revalidate=86400) so Vercel's CDN serves a cached copy
 * for an hour and tolerates a day of staleness during regenerations.
 * Segment-config `revalidate` would conflict with this on a Route
 * Handler, so it's intentionally not exported.
 */
export async function GET(): Promise<Response> {
  let shards: { id: number }[];
  try {
    const planned = await planSitemapShards();
    shards = planned.length > 0 ? planned : [{ id: 0 }];
  } catch {
    // planSitemapShards already swallows fetcher errors and falls
    // back to [{id:0}], so this catch is defense-in-depth for any
    // future change that makes the planner throw.
    shards = [{ id: 0 }];
  }

  // <lastmod> on each <sitemap> entry tells crawlers when to re-fetch
  // the child. Without it, Google can stick on a stale view of the
  // index ("0 discovered pages") until it eventually decides to
  // re-walk every child — days on a large index. Tying lastmod to the
  // cache-fill window (1h s-maxage) gives a steady "this index moves
  // hourly, re-walk the children" signal without per-request churn.
  const lastmod = new Date().toISOString();

  const lines: string[] = [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">`,
  ];
  // Iterate the planner's ids verbatim instead of regenerating 0..N.
  // The current planner emits contiguous ids, but the loop staying
  // honest to its input means a future change won't silently emit
  // shard URLs that don't exist.
  for (const { id } of shards) {
    lines.push(
      `  <sitemap><loc>${siteConfig.url}/sitemap/${id}.xml</loc><lastmod>${lastmod}</lastmod></sitemap>`,
    );
  }
  lines.push(`</sitemapindex>`, "");

  return new Response(lines.join("\n"), {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
