import { ImageResponse } from "next/og";
import { unstable_cache } from "next/cache";
import { getCompanyBySlug, type CompanyDetail } from "@/lib/actions/company";
import {
  companyOgCacheKey,
  readCompanyOgCache,
  shouldBypassCompanyOgCache,
  writeCompanyOgCache,
} from "@/lib/og/company-og-cache";

export const alt = "Company jobs";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const dynamic = "force-dynamic";
// Long-cache via explicit headers. The durable cache key includes a
// renderer hash, so unchanged deploys reuse R2 PNGs and renderer changes
// naturally move to a new key.
const CACHE_HEADERS = {
  "Content-Type": contentType,
  "Cache-Control": "public, max-age=2592000, s-maxage=2592000, immutable",
};

import { readFileSync } from "node:fs";
import { join } from "node:path";

const COMPANY_OG_CACHE_TTL_SECONDS = 2592000;

const getCachedOgCompany = unstable_cache(
  async (slug: string, lang: string) => getCompanyBySlug(slug, lang),
  ["company-opengraph"],
  { revalidate: COMPANY_OG_CACHE_TTL_SECONDS },
);

async function getOgCompany(slug: string, lang: string): Promise<CompanyDetail | null> {
  try {
    return await getCachedOgCompany(slug, lang);
  } catch (error) {
    if (
      error instanceof Error &&
      error.message.includes("incrementalCache missing")
    ) {
      return getCompanyBySlug(slug, lang);
    }
    throw error;
  }
}

// Satori only supports TTF/OTF, not woff2. Keep this synchronous at module
// load so the static renderer does not see uncached async filesystem IO.
const fontData = readFileSync(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

function asPngResponse(bytes: Uint8Array): Response {
  const body = new Uint8Array(bytes.byteLength);
  body.set(bytes);
  return new Response(body.buffer, { headers: CACHE_HEADERS });
}

async function imageResponseToBytes(response: Response): Promise<Uint8Array> {
  return new Uint8Array(await response.arrayBuffer());
}

function renderNotFound(fontData: Buffer): ImageResponse {
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
    {
      ...size,
      headers: CACHE_HEADERS,
      fonts: [{ name: "JetBrains Mono", data: fontData, weight: 700, style: "normal" }],
    },
  );
}

function renderCompanyImage(company: CompanyDetail, fontData: Buffer): ImageResponse {
  const hasIcon =
    company.icon &&
    company.icon.startsWith("http") &&
    !company.icon.toLowerCase().endsWith(".webp");

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

  const company = await getOgCompany(slug, lang);
  if (!company) {
    return renderNotFound(fontData);
  }

  const rendered = renderCompanyImage(company, fontData);
  const bytes = await imageResponseToBytes(rendered);
  await writeCompanyOgCache(key, bytes);
  return asPngResponse(bytes);
}
