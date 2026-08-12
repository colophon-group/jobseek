import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const SIZE = { width: 1200, height: 630 } as const;

// Satori (used by next/og) only supports TTF/OTF, not woff2. This module is
// imported only by the off-platform prewarmer; it is deliberately unreachable
// from the Next.js app runtime and therefore absent from ordinary page traces.
const fontPromise = readFile(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

const logoPromise = readFile(
  join(process.cwd(), "public", "android-chrome-512x512.png"),
).then((buf) => `data:image/png;base64,${buf.toString("base64")}`);

export async function renderSiteOgCard(): Promise<ImageResponse> {
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
      {/* next/image is not supported inside Satori's render tree. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={logoSrc} width={120} height={120} alt="" />
      <span style={{ fontSize: 56, fontWeight: 700 }}>Job Seek</span>
      <span style={{ fontSize: 26, color: "#a1a1aa" }}>
        Track the companies you actually want to work at
      </span>
    </div>,
    {
      ...SIZE,
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
