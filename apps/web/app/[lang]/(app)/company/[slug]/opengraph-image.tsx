import { ImageResponse } from "next/og";
import { getCompanyBySlug } from "@/lib/actions/company";
import { locales } from "@/lib/i18n";

export const alt = "Company jobs";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// 30 days. Company logo/name/description change far less often than
// daily — a 1-day revalidate multiplied by ~16k (slug × locale) cells
// churned OG regenerations against the crawl surge. Bump high; a
// rename/rebrand still propagates within a month, and deploys purge
// the cache anyway.
export const revalidate = 2592000;

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
 * Pick the top-N companies to prerender. Returns `[]` on any error so a
 * build environment without Typesense access (or a transient outage)
 * never fails the build — Next.js will fall back to dynamic rendering on
 * first request, identical to the existing behavior.
 */
export async function generateStaticParams(): Promise<
  { lang: string; slug: string }[]
> {
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
    return slugs.flatMap((slug) => locales.map((lang) => ({ lang, slug })));
  } catch {
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
      { ...size },
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
