import { ImageResponse } from "next/og";
import { getCompanyBySlug } from "@/lib/actions/company";

export const alt = "Company jobs";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

import { readFile } from "node:fs/promises";
import { join } from "node:path";

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
