import { ImageResponse } from "next/og";
import { getCompanyBySlug } from "@/lib/actions/company";
import { locales } from "@/lib/i18n";

export const alt = "Company jobs";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// Long-cache (30 days) via explicit `Cache-Control` headers on the
// ImageResponse — `'use cache'` doesn't apply (ImageResponse is a
// class instance, not serializable for the runtime cache), and Next.js
// doesn't auto-cache OG images outside the prerender window. Vercel
// purges the CDN on every deploy so `immutable` is safe.
// `generateStaticParams` below covers the top-N companies at build
// time so social-card crawl surges land on the prebake; long-tail
// slugs render once per region per 30 days.
const CACHE_HEADERS = {
  "Cache-Control": "public, max-age=2592000, s-maxage=2592000, immutable",
};

import { readFile } from "node:fs/promises";
import { join } from "node:path";

/**
 * How many top companies (by active posting count) to prerender at build
 * time, across every supported locale. The actual count of cells baked
 * into the build is `OG_PRERENDER_TOP_N × locales.length` (4 today). At
 * ~50 KB per PNG that's a ~40 MB increase to the build artifact for N=200.
 *
 * Long-tail companies still generate on first request and then live in
 * Vercel's CDN for the `revalidate` window. Prebaking the top tier
 * absorbs Twitter/LinkedIn/Slack crawl spikes that otherwise cold-start
 * a function per (slug, locale).
 *
 * See issue #2645.
 */
const OG_PRERENDER_TOP_N = 200;

/**
 * Pick the top-N companies to prerender. Fails the build if Typesense
 * is configured (`TYPESENSE_HOST` set) but unreachable on a production
 * build — silently shipping a deploy with zero OG prerender means every
 * Twitter/LinkedIn/Slack crawl on a popular slug cold-starts a function,
 * which is exactly the cost surge the prebake exists to absorb.
 *
 * Soft-fails (returns `[]`) when Typesense isn't configured at all
 * (preview deploys without the secret, local builds without `.env.local`)
 * — those don't have access to the Typesense index, and dynamic-render
 * fallback is the only sensible behavior. See #2835 critic round 2.
 */
export async function generateStaticParams(): Promise<
  { lang: string; slug: string }[]
> {
  const isProductionBuild = process.env.VERCEL_ENV === "production";
  const hasTypesenseConfig = !!process.env.TYPESENSE_HOST;
  try {
    const { getSearchClient } = await import(
      "@/lib/search/typesense-client"
    );
    const client = getSearchClient();
    const result = await client.collections("company").documents().search({
      q: "*",
      query_by: "name",
      filter_by: "active_posting_count:>0",
      sort_by: "active_posting_count:desc",
      per_page: OG_PRERENDER_TOP_N,
      page: 1,
      include_fields: "slug",
    });
    const slugs = (result.hits ?? [])
      .map((h) => (h.document as Record<string, unknown>).slug)
      .filter((s): s is string => typeof s === "string");
    if (slugs.length === 0 && isProductionBuild && hasTypesenseConfig) {
      throw new Error(
        "[opengraph-image] generateStaticParams: 0 companies returned from Typesense " +
          "on a production build with TYPESENSE_HOST set — would silently degrade to " +
          "per-request OG generation. Check Typesense reachability + " +
          "active_posting_count:>0 filter.",
      );
    }
    return slugs.flatMap((slug) => locales.map((lang) => ({ lang, slug })));
  } catch (err) {
    if (isProductionBuild && hasTypesenseConfig) throw err;
    // Typesense not configured (preview without secrets) OR local build —
    // log loudly but don't fail; long-tail dynamic rendering still works.
    console.warn(
      "[opengraph-image] generateStaticParams: skipping prerender",
      err,
    );
    return [];
  }
}

// Satori only supports TTF/OTF, not woff2.
const fontPromise = readFile(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

export default async function OgImage({
  params,
}: {
  params: Promise<{ lang: string; slug: string }>;
}) {
  const { slug, lang } = await params;
  const company = await getCompanyBySlug(slug, lang);
  if (!company) {
    return new ImageResponse(
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#0a0a0a",
          color: "#fafafa",
          fontSize: 48,
          fontFamily: "JetBrains Mono",
        }}
      >
        Not Found
      </div>,
      { ...size, headers: CACHE_HEADERS },
    );
  }

  const fontData = await fontPromise;
  const hasIcon = company.icon && company.icon.startsWith("http");

  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "#0a0a0a",
        color: "#fafafa",
        fontFamily: "JetBrains Mono",
        padding: "60px 80px",
      }}
    >
      {/* Top: company icon + name */}
      <div style={{ display: "flex", alignItems: "center", gap: "24px" }}>
        {hasIcon && (
          <img
            src={company.icon!}
            width={72}
            height={72}
            style={{ borderRadius: 12 }}
          />
        )}
        <span style={{ fontSize: 52, fontWeight: 700 }}>{company.name}</span>
      </div>

      {/* Middle: description */}
      {company.description && (
        <div
          style={{
            fontSize: 28,
            color: "#a1a1aa",
            marginTop: 32,
            lineHeight: 1.4,
            overflow: "hidden",
            display: "flex",
            maxHeight: "160px",
          }}
        >
          {company.description.length > 200
            ? company.description.slice(0, 200) + "…"
            : company.description}
        </div>
      )}

      {/* Bottom: meta chips */}
      <div
        style={{
          display: "flex",
          gap: "16px",
          marginTop: "auto",
          fontSize: 22,
          color: "#71717a",
        }}
      >
        {company.industryName && <span>{company.industryName}</span>}
        {company.industryName && company.website && <span>·</span>}
        {company.website && (
          <span>{company.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}</span>
        )}
      </div>

      {/* Branding */}
      <div
        style={{
          position: "absolute",
          bottom: 40,
          right: 80,
          fontSize: 20,
          color: "#52525b",
          display: "flex",
        }}
      >
        jseek.co
      </div>
    </div>,
    {
      ...size,
      headers: CACHE_HEADERS,
      fonts: [
        {
          name: "JetBrains Mono",
          data: fontData,
          weight: 700,
          style: "normal",
        },
      ],
    },
  );
}
