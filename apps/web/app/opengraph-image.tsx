import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const alt = "Job Seek";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Default OG image for any route that doesn't have a more specific
// `opengraph-image.tsx`. Lives at the root so Next.js's metadata API
// auto-discovery picks it up for `/[lang]/...` pages whose own
// `generateMetadata` overrides `openGraph` without an `images` field
// (route-group OG resolution doesn't reliably walk to `[lang]` segments
// — keeping this at root sidesteps the lookup ambiguity).
//
// The og:image URL Next.js generates is `/opengraph-image-<hash>`,
// without a locale prefix. The locale-redirect middleware excludes this
// path so the response goes to this handler directly without a 308 to
// `/<locale>/opengraph-image-<hash>` (which would 404). See
// `apps/web/middleware.ts:52`.
//
// Long-cache via explicit Cache-Control headers; Vercel CDN is purged
// on every deploy so `immutable` is safe.
const CACHE_HEADERS = {
  "Cache-Control": "public, max-age=2592000, s-maxage=2592000, immutable",
};

// Satori (used by next/og) only supports TTF/OTF, not woff2.
const fontPromise = readFile(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

const logoPromise = readFile(
  join(process.cwd(), "public", "android-chrome-512x512.png"),
).then((buf) => `data:image/png;base64,${buf.toString("base64")}`);

export default async function OgImage() {
  const [fontData, logoSrc] = await Promise.all([fontPromise, logoPromise]);

  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "#0a0a0a",
        color: "#fafafa",
        fontFamily: "JetBrains Mono",
        gap: "24px",
      }}
    >
      <img src={logoSrc} width={120} height={120} />
      <span style={{ fontSize: 56, fontWeight: 700 }}>Job Seek</span>
      <span style={{ fontSize: 26, color: "#a1a1aa" }}>
        Track the companies you actually want to work at
      </span>
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
