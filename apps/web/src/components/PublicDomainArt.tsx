"use client";

import { useContext } from "react";
import Image from "next/image";
import type { CSSProperties, ReactNode } from "react";
import { useLingui } from "@lingui/react/macro";
import { ThemeContext } from "@/components/ThemeProvider";
import Box from "@mui/material/Box";
import type { SxProps, Theme } from "@mui/material/styles";
import type { PublicDomainAsset, CropInsets } from "@/content/config";

type PublicDomainArtProps = {
  asset: PublicDomainAsset;
  focus?: {
    x?: number;
    y?: number;
  };
  crop?: CropInsets;
  credit?: boolean;
  sx?: SxProps<Theme>;
  children?: ReactNode;
};

export function PublicDomainArt({
  asset,
  focus,
  crop,
  credit = true,
  sx,
  children,
}: PublicDomainArtProps) {
  const { mode } = useContext(ThemeContext);
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
    <Box
      sx={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 2,
        boxShadow: "0px 8px 24px rgba(15, 23, 42, 0.16)",
        minHeight: "100%",
        ...sx,
      }}
    >
      {hasCrop ? (
        <Box
          sx={{
            position: "absolute",
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
        </Box>
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
        <Box
          component="a"
          href={link ?? href ?? src}
          target="_blank"
          rel="noreferrer"
          sx={{
            position: "absolute",
            right: 16,
            bottom: 16,
            fontSize: "0.75rem",
            letterSpacing: 0.2,
            textTransform: "uppercase",
            fontWeight: 600,
            color: "rgba(255,255,255,0.9)",
            textDecoration: "none",
            backgroundColor: "rgba(0,0,0,0.6)",
            borderRadius: 999,
            px: 1.5,
            py: 0.5,
            backdropFilter: "blur(6px)",
          }}
        >
          {title ? `${title}` : creditFallback}
          {author ? ` · ${author}` : ""}
          {date ? ` (${date})` : ""}
        </Box>
      )}
    </Box>
  );
}
