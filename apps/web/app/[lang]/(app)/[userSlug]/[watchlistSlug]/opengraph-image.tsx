import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { getPublicWatchlistByUserAndSlug } from "@/lib/actions/watchlists";

export const alt = "Watchlist on Job Seek";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// 1-day cache via explicit `Cache-Control` headers on the
// ImageResponse — `'use cache'` doesn't apply (ImageResponse is a
// class instance).
//
// Unlike the company OG (which is `immutable` for 30 days because
// companies don't toggle visibility on the web), watchlists CAN be
// flipped from public to private at any time by their owner. PR #2888
// busts the page-level `'use cache'` and Redis on toggle, but social
// previews on Twitter/LinkedIn/Slack/Facebook fetch the OG card while
// the watchlist was public and respect `Cache-Control` themselves —
// `immutable` would tell them never to revalidate, leaking the card
// for up to 30 days after privacy toggle. Drop `immutable` and cap
// max-age at 1 day so third-party social caches refetch within 24h
// of any visibility change. Issue #2890 (privacy follow-up to #2888).
const CACHE_HEADERS = {
  "Cache-Control": "public, max-age=86400, s-maxage=86400",
};

// Satori (used by next/og) only supports TTF/OTF, not woff2.
const fontPromise = readFile(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

function countFilters(
  filters: { keywords?: unknown[]; locationSlugs?: unknown[]; occupationSlugs?: unknown[]; senioritySlugs?: unknown[]; technologySlugs?: unknown[] } | null | undefined,
): number {
  const f = filters ?? {};
  return (f.keywords?.length ?? 0)
    + (f.locationSlugs?.length ?? 0)
    + (f.occupationSlugs?.length ?? 0)
    + (f.senioritySlugs?.length ?? 0)
    + (f.technologySlugs?.length ?? 0);
}

export default async function OgImage({
  params,
}: {
  params: Promise<{ lang: string; userSlug: string; watchlistSlug: string }>;
}) {
  const { userSlug, watchlistSlug } = await params;
  const detail = await getPublicWatchlistByUserAndSlug(userSlug, watchlistSlug);
  const fontData = await fontPromise;

  if (!detail) {
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

  const ownerLabel = detail.owner.displayUsername
    ?? detail.owner.username
    ?? detail.owner.name;
  const companyCount = detail.companies.length;
  const filterCount = countFilters(detail.filters);

  // Build a small meta line: "@owner · 12 companies · 3 filters". Skip
  // segments that would be zero so the chip doesn't lie. For
  // `anyCompany` watchlists `companyCount` is misleading (leftover
  // rows from source copies) — emit "anyCompany" instead.
  const metaParts: string[] = [`@${ownerLabel}`];
  if (detail.filters.anyCompany) {
    metaParts.push("any company");
  } else if (companyCount > 0) {
    metaParts.push(
      `${companyCount} compan${companyCount === 1 ? "y" : "ies"}`,
    );
  }
  if (filterCount > 0) {
    metaParts.push(`${filterCount} filter${filterCount === 1 ? "" : "s"}`);
  }
  const metaLine = metaParts.join(" · ");

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
      <span style={{ fontSize: 28, color: "#a1a1aa" }}>Watchlist</span>

      <span
        style={{
          fontSize: 60,
          fontWeight: 700,
          marginTop: 18,
          lineHeight: 1.15,
          // Long titles overflow the 1200-wide canvas. Three lines is
          // the practical cap before the meta line / branding clip.
          maxHeight: 60 * 1.15 * 3,
          overflow: "hidden",
          display: "flex",
        }}
      >
        {detail.title}
      </span>

      {detail.description && (
        <div
          style={{
            fontSize: 26,
            color: "#a1a1aa",
            marginTop: 24,
            lineHeight: 1.4,
            overflow: "hidden",
            display: "flex",
            maxHeight: "120px",
          }}
        >
          {detail.description.length > 180
            ? detail.description.slice(0, 180) + "…"
            : detail.description}
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: "16px",
          marginTop: "auto",
          fontSize: 22,
          color: "#71717a",
        }}
      >
        {metaLine}
      </div>

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
