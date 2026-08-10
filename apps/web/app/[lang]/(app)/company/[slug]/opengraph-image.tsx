import {
  companyOgCacheKey,
  readCompanyOgCache,
  shouldBypassCompanyOgCache,
  writeCompanyOgCache,
} from "@/lib/og/company-og-cache";

export const alt = "Company jobs";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Request-time R2 IO is intentional. Making this route static caused
// DYNAMIC_SERVER_USAGE failures in production under Cache Components.
export const dynamic = "force-dynamic";

const CACHE_CONTROL = "public, max-age=2592000, s-maxage=2592000, immutable";
const PNG_HEADERS = {
  "Content-Type": contentType,
  "Cache-Control": CACHE_CONTROL,
};

function asPngResponse(bytes: Uint8Array): Response {
  const body = new Uint8Array(bytes.byteLength);
  body.set(bytes);
  return new Response(body.buffer, { headers: PNG_HEADERS });
}

export default async function OgImage({
  params,
}: {
  params: Promise<{ lang: string; slug: string }>;
}) {
  const { slug, lang } = await params;
  const key = companyOgCacheKey(lang, slug);

  if (!shouldBypassCompanyOgCache()) {
    const cached = await readCompanyOgCache(key);
    if (cached) return asPngResponse(cached);
  }

  // Keep next/og, the font, company services, and the AWS SDK out of the hot
  // cache-hit module path. They are evaluated only when this renderer version
  // has never produced the requested card.
  const { renderCompanyOgImage } = await import("@/lib/og/render-company-og");
  const rendered = await renderCompanyOgImage(slug, lang);
  const bytes = new Uint8Array(await rendered.response.arrayBuffer());

  if (rendered.cacheable) {
    await writeCompanyOgCache(key, bytes);
  }
  return asPngResponse(bytes);
}
