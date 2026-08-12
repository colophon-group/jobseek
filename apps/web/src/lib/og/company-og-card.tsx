import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import type { CompanyDetail } from "@/lib/services/company-detail-lookup";

const CACHE_HEADERS = {
  "Content-Type": "image/png",
  "Cache-Control": "public, max-age=2592000, s-maxage=2592000, immutable",
};
const size = { width: 1200, height: 630 };

// Satori only supports TTF/OTF, not woff2. Keeping the font next to the pure
// card renderer lets both Next.js and the GitHub prewarmer produce identical
// bytes without importing the application data layer.
const fontData = readFileSync(
  join(process.cwd(), "public/fonts/JetBrainsMono-Bold.ttf"),
);

const RENDERABLE_COMPANY_OG_ICON_EXTENSIONS = new Set([".png", ".jpg", ".jpeg"]);

type CompanyOgIconRenderModel =
  | { kind: "image"; src: string }
  | { kind: "fallback"; label: string }
  | { kind: "none" };

function parseHttpUrl(value: string | null | undefined): URL | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

export function getRenderableCompanyOgIconUrl(
  icon: string | null | undefined,
): string | null {
  const url = parseHttpUrl(icon);
  if (!url) return null;

  const pathname = url.pathname.toLowerCase();
  for (const extension of RENDERABLE_COMPANY_OG_ICON_EXTENSIONS) {
    if (pathname.endsWith(extension)) return icon!;
  }
  return null;
}

export function getCompanyOgFallbackInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return parts
      .slice(0, 2)
      .map((part) => Array.from(part)[0])
      .join("")
      .toUpperCase();
  }

  const firstPart = parts[0] ?? "";
  return Array.from(firstPart).slice(0, 2).join("").toUpperCase() || "?";
}

export function getCompanyOgIconRenderModel(
  company: Pick<CompanyDetail, "icon" | "name">,
): CompanyOgIconRenderModel {
  const src = getRenderableCompanyOgIconUrl(company.icon);
  if (src) return { kind: "image", src };

  // next/og logs noisy parse errors for unsupported SVG/WebP inputs. Keep the
  // card deterministic instead of handing those URLs to Satori.
  if (parseHttpUrl(company.icon)) {
    return { kind: "fallback", label: getCompanyOgFallbackInitials(company.name) };
  }

  return { kind: "none" };
}

export function renderCompanyOgNotFoundCard(): ImageResponse {
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
      fonts: [
        { name: "JetBrains Mono", data: fontData, weight: 700, style: "normal" },
      ],
    },
  );
}

export function renderCompanyOgCard(company: CompanyDetail): ImageResponse {
  const icon = getCompanyOgIconRenderModel(company);

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
      <div style={{ display: "flex", alignItems: "center", gap: "24px" }}>
        {icon.kind === "image" && (
          // next/image cannot render inside next/og's Satori tree.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={icon.src}
            width={72}
            height={72}
            style={{ borderRadius: 12 }}
          />
        )}
        {icon.kind === "fallback" && (
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: 12,
              backgroundColor: "#18181b",
              border: "1px solid #27272a",
              color: "#fafafa",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 28,
              fontWeight: 700,
              lineHeight: 1,
            }}
          >
            {icon.label}
          </div>
        )}
        <span style={{ fontSize: 52, fontWeight: 700 }}>{company.name}</span>
      </div>

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
