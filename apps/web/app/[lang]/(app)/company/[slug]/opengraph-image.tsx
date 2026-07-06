import { ImageResponse } from "next/og";
import { unstable_cache } from "next/cache";
import { getCompanyBySlug, type CompanyDetail } from "@/lib/actions/company";
import { locales } from "@/lib/i18n";
import {
  companyOgCacheKey,
  readCompanyOgCache,
  shouldBypassCompanyOgCache,
  writeCompanyOgCache,
} from "@/lib/og/company-og-cache";

export const alt = "Company jobs";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
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

/**
 * How many top companies (by active posting count) to prerender at build
 * time, across every supported locale. This is opt-in: the default is 0
 * because each company slug fans out across every locale and each generated
 * image may need a company-detail read. At N=200 that is 800 route renders.
 *
 * Long-tail companies still generate on first request and then live in
 * Vercel's CDN for the `revalidate` window. Operators can prebake a bounded
 * top tier for a specific deploy by setting `COMPANY_OG_PRERENDER_TOP_N`.
 *
 * See issues #2645 and #3422.
 */
function getCompanyOgPrerenderTopN(): number {
  const raw = process.env.COMPANY_OG_PRERENDER_TOP_N;
  if (!raw) return 0;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(parsed, 500));
}

/**
 * Pick the top-N companies to prerender. Fails the build only when
 * Typesense is configured AND unreachable on a production build —
 * that's the failure mode where a silent zero-prerender translates
 * into every Twitter/LinkedIn/Slack crawl cold-starting a function.
 *
 * Soft-fails (returns `[]` + warn) in two other cases:
 *   1. Typesense isn't configured (preview deploys without secrets,
 *      local builds without `.env.local`).
 *   2. Typesense responded successfully but with 0 results — could be
 *      a legitimately empty index (during reindex / fresh deploy) or
 *      an `active_posting_count:>0` filter mismatch. Failing the
 *      production deploy on a transient empty-index state would couple
 *      Vercel deploy availability to a specific Typesense data shape.
 *
 * See #2835 critic rounds 2 and 3.
 */
export async function generateStaticParams(): Promise<
  { lang: string; slug: string }[]
> {
  const prerenderTopN = getCompanyOgPrerenderTopN();
  if (prerenderTopN === 0) return [];

  const isProductionBuild = process.env.VERCEL_ENV === "production";
  const hasTypesenseConfig = !!process.env.TYPESENSE_HOST;
  try {
    const [{ getSearchClient }, {
      isRetryableError,
      isTypesenseRateLimitError,
      withTypesenseRetry,
    }] = await Promise.all([
      import("@/lib/search/typesense-client"),
      import("@/lib/search/typesense-retry"),
    ]);
    const client = getSearchClient();
    const result = await withTypesenseRetry(
      () =>
        client.collections("company").documents().search({
          q: "*",
          query_by: "name",
          filter_by: "active_posting_count:>0",
          sort_by: "active_posting_count:desc",
          per_page: prerenderTopN,
          page: 1,
          include_fields: "slug",
        }),
      {
        attempts: 5,
        baseDelaysMs: [250, 500, 1000, 2000],
        isRetryable: (err) => isRetryableError(err) || isTypesenseRateLimitError(err),
        label: "company-og.generateStaticParams",
      },
    );
    const slugs = (result.hits ?? [])
      .map((h) => (h.document as Record<string, unknown>).slug)
      .filter((s): s is string => typeof s === "string");
    if (slugs.length === 0) {
      console.warn(
        "[opengraph-image] generateStaticParams: 0 companies returned from " +
          "Typesense — index empty or filter mismatch. Long-tail OG generation " +
          "will fall back to per-request rendering.",
      );
    }
    return slugs.flatMap((slug) => locales.map((lang) => ({ lang, slug })));
  } catch (err) {
    if (isProductionBuild && hasTypesenseConfig) {
      // Typesense was configured but the call threw (network error,
      // misconfigured secret, etc.) on a production build. Fail loud —
      // silently shipping zero prerender hides a real outage.
      throw err;
    }
    // Typesense not configured (preview without secrets) OR local build —
    // log loudly but don't fail; dynamic OG rendering still works.
    console.warn(
      "[opengraph-image] generateStaticParams: skipping prerender",
      err,
    );
    return [];
  }
}

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
