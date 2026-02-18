"use client";

import Image from "next/image";
import type { CSSProperties, ReactNode } from "react";
import { useLingui } from "@lingui/react/macro";
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
  const { t } = useLingui();
  const { light, dark, href, alt, width, height, title, author, date, link, crop: assetCrop } = asset;

  const lightSrc = light ?? dark ?? href;
  const darkSrc = dark ?? light ?? href;
  if (!lightSrc && !darkSrc) {
    return null;
  }

  const objectPosition = `${focus?.x ?? 50}% ${focus?.y ?? 50}%`;
  const appliedCrop = crop ?? assetCrop;

  const imageCropStyle = (() => {
    if (!appliedCrop) return {} as CSSProperties;
    const { top = 0, right = 0, bottom = 0, left = 0 } = appliedCrop;
    const effectiveWidth = Math.max(1, width - left - right);
    const effectiveHeight = Math.max(1, height - top - bottom);
    const scaleX = width / effectiveWidth;
    const scaleY = height / effectiveHeight;
    const translateXPct = (left / width) * 100;
    const translateYPct = (top / height) * 100;
    if (scaleX === 1 && scaleY === 1 && translateXPct === 0 && translateYPct === 0) {
      return {} as CSSProperties;
    }
    return {
      transformOrigin: "top left",
      transform: `translate(${-translateXPct}%, ${-translateYPct}%) scale(${scaleX}, ${scaleY})`,
    } as CSSProperties;
  })();

  const imageStyle: CSSProperties = {
    objectFit: "cover",
    objectPosition,
    ...imageCropStyle,
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
      {/* Light mode image */}
      {lightSrc && (
        <Image
          src={lightSrc}
          alt={alt}
          fill
          sizes="(min-width: 1024px) 40vw, 100vw"
          style={{
            ...imageStyle,
            display: "var(--pda-light-display, block)",
          }}
          priority={false}
        />
      )}
      {/* Dark mode image */}
      {darkSrc && darkSrc !== lightSrc && (
        <Image
          src={darkSrc}
          alt=""
          aria-hidden
          fill
          sizes="(min-width: 1024px) 40vw, 100vw"
          style={{
            ...imageStyle,
            display: "var(--pda-dark-display, none)",
          }}
          priority={false}
        />
      )}
      {children}
      {credit && (title || author) && (
        <Box
          component="a"
          href={link ?? href ?? lightSrc ?? darkSrc}
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
