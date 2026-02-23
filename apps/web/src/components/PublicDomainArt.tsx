"use client";

import { useState, useEffect } from "react";
import { useTheme } from "next-themes";
import Image from "next/image";
import type { CSSProperties, ReactNode } from "react";
import { useLingui } from "@lingui/react/macro";
import { Trans } from "@lingui/react/macro";
import type { PublicDomainAsset, CropInsets } from "@/content/config";

type PublicDomainArtProps = {
  asset: PublicDomainAsset;
  focus?: {
    x?: number;
    y?: number;
  };
  crop?: CropInsets;
  credit?: boolean;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
};

export function PublicDomainArt({
  asset,
  focus,
  crop,
  credit = true,
  className,
  style,
  children,
}: PublicDomainArtProps) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const mode = mounted ? (resolvedTheme ?? "dark") : "dark";

  const { t } = useLingui();
  const { light, dark, href, alt, width, height, title, author, date, link, crop: assetCrop } = asset;

  const src = (mode === "dark" ? (dark ?? light) : (light ?? dark)) ?? href;
  if (!src) {
    return null;
  }

  const objectPosition = `${focus?.x ?? 50}% ${focus?.y ?? 50}%`;
  const appliedCrop = crop ?? assetCrop;
  const { top: cropTop = 0, right: cropRight = 0, bottom: cropBottom = 0, left: cropLeft = 0 } = appliedCrop ?? {};
  const hasCrop = cropTop > 0 || cropRight > 0 || cropBottom > 0 || cropLeft > 0;
  const effectiveWidth = Math.max(1, width - cropLeft - cropRight);
  const effectiveHeight = Math.max(1, height - cropTop - cropBottom);

  const imageStyle: CSSProperties = {
    objectFit: "cover",
    objectPosition,
  };

  const creditFallback = t({
    id: "common.art.publicDomain",
    comment: "Fallback credit text for public domain artwork",
    message: "Public Domain",
  });

  return (
    <div
      className={`relative min-h-full overflow-hidden rounded-md shadow-[0px_8px_24px_rgba(15,23,42,0.16)] ${className ?? ""}`}
      style={style}
    >
      {hasCrop ? (
        <div
          className="absolute"
          style={{
            width: `${(width / effectiveWidth) * 100}%`,
            height: `${(height / effectiveHeight) * 100}%`,
            top: `${-(cropTop / effectiveHeight) * 100}%`,
            left: `${-(cropLeft / effectiveWidth) * 100}%`,
          }}
        >
          <Image
            src={src}
            alt={alt}
            fill
            sizes="(min-width: 1024px) 40vw, 100vw"
            style={imageStyle}
            priority={false}
          />
        </div>
      ) : (
        <Image
          src={src}
          alt={alt}
          fill
          sizes="(min-width: 1024px) 40vw, 100vw"
          style={imageStyle}
          priority={false}
        />
      )}
      {children}
      {credit && (title || author) && (
        <a
          href={link ?? href ?? src}
          target="_blank"
          rel="noreferrer"
          className="absolute right-4 bottom-4 rounded-full bg-black/60 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-white/90 no-underline backdrop-blur-sm"
        >
          {title ? `${title}` : creditFallback}
          {author ? ` · ${author}` : ""}
          {date ? ` (${date})` : ""}
          <span className="sr-only"><Trans id="common.a11y.opensInNewTab" comment="Screen reader text for external links">(opens in new tab)</Trans></span>
        </a>
      )}
    </div>
  );
}
