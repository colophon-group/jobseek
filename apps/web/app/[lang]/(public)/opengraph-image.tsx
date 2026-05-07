import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const alt = "Job Seek — Track the companies you actually want to work at";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// Long-cache via explicit Cache-Control headers; deploys purge.
const CACHE_HEADERS = {
  "Cache-Control": "public, max-age=2592000, s-maxage=2592000, immutable",
};

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
        padding: "60px",
      }}
    >
      <img src={logoSrc} width={100} height={100} />
      <span style={{ fontSize: 48, fontWeight: 700, textAlign: "center" }}>
        Track the companies you actually want to work at
      </span>
      <span style={{ fontSize: 22, color: "#a1a1aa", textAlign: "center" }}>
        Build watchlists. Get email alerts. Postings sourced directly from company career pages.
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
