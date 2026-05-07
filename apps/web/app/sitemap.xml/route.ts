import type { MetadataRoute } from "next";
import {
  planSitemapShards,
  renderSitemapShard,
  serializeUrlset,
} from "@/lib/sitemap";

/**
 * /sitemap.xml — TEMPORARY: monolithic <urlset>.
 *
 * Reverts when the GSC investigation finishes. Background:
 * after the index + lastmod + robots.txt fixes shipped in #2817 /
 * #2819, GSC continued to report "Discovered pages: 0" on the
 * sitemap index AND "Couldn't fetch" when the user submitted
 * /sitemap/0.xml directly. Every reachability angle we can test
 * (multiple bot UAs, HTTP/1.1, HEAD, gzip, br, conditional GET,
 * cold/warm cache) returns a clean 200 with valid XML in <300ms,
 * and there are no anti-bot mechanisms in front of jseek.co.
 *
 * The remaining hypothesis is something specific to GSC's
 * sitemap-index recursion. To isolate it, this temporarily
 * collapses every URL into a single <urlset> served at
 * /sitemap.xml. The /sitemap/<id>.xml shard routes still work
 * (they're harmless side-by-side); robots.txt only declares the
 * monolithic URL while this experiment runs.
 *
 * Capacity: ~17K URLs / ~10 MB total — well under the sitemap.org
 * limits of 50K URLs / 50 MB uncompressed. Vercel auto-compresses
 * XML at the edge so the on-the-wire payload is far smaller.
 *
 * REVERT: replace the body with the previous sitemapindex render
 * (git show {commit-sha}:apps/web/app/sitemap.xml/route.ts) and
 * restore robots.ts to declare every shard.
 */
export async function GET(): Promise<Response> {
  let entries: MetadataRoute.Sitemap = [];
  try {
    const shards = await planSitemapShards();
    // Render shards in parallel; the data layer's Redis cache + per-key
    // single-flight (see lib/cache.ts) ensures the underlying Typesense /
    // Postgres queries fan in to one upstream call regardless of N.
    // `renderSitemapShard` returns null for ids outside the current
    // plan (a defensive 404 path); planSitemapShards never hands one
    // here today, but the filter keeps the types honest if the planner
    // ever loosens.
    const renderedShards = await Promise.all(
      shards.map(({ id }) => renderSitemapShard(id)),
    );
    entries = renderedShards
      .filter((shard): shard is MetadataRoute.Sitemap => shard !== null)
      .flat();
  } catch {
    // Defense-in-depth: serializeUrlset emits a syntactically valid
    // empty <urlset/> rather than letting the route 500 — crawlers
    // re-try on retryable errors but de-rank on hard failures.
  }

  const xml = serializeUrlset(entries);

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
