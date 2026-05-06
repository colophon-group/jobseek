import { renderSitemapShard, serializeUrlset } from "@/lib/sitemap";

/**
 * Per-shard sitemap at `/sitemap/<id>.xml`.
 *
 * The dynamic segment captures the literal URL value, so for
 * `/sitemap/0.xml` the param is `id = "0.xml"`. We strip the `.xml`
 * suffix, parse the integer, and dispatch to the shard renderer in
 * `@/lib/sitemap`. Anything that doesn't match the `<integer>.xml`
 * shape is rejected as 404 — the shape is part of our contract with
 * the sitemap index in `app/sitemap.xml/route.ts`. A shape-valid id
 * for which `renderSitemapShard` returns `null` (no such shard in
 * the current plan) also 404s — this is what stale-cache requests
 * for retired shards (e.g. company shards before #2821) now see.
 *
 * Caching mirrors the index handler: explicit Cache-Control with a
 * long stale-while-revalidate window so brief regen latency doesn't
 * surface as crawler 5xx.
 */
const SHARD_PATTERN = /^(\d+)\.xml$/;

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id: rawId } = await params;
  const match = SHARD_PATTERN.exec(rawId);
  if (!match) {
    return new Response("Not Found", { status: 404 });
  }
  const id = Number.parseInt(match[1], 10);

  const entries = await renderSitemapShard(id);
  if (entries === null) {
    return new Response("Not Found", { status: 404 });
  }
  const xml = serializeUrlset(entries);

  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
