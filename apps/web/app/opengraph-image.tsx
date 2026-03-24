import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const alt = "Job Seek";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const fontPromise = readFile(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.woff2"),
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
        Find relevant roles faster
      </span>
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
