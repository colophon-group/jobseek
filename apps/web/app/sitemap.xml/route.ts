import { buildSitemap, serializeUrlset } from "@/lib/sitemap";

/** Public marketing pages and translated articles; product/account pages are noindex. */
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
