"use client";

import { useState, useEffect } from "react";
import { useTheme } from "next-themes";
import Image from "next/image";
import type { CSSProperties } from "react";

type ThemedImageProps = {
  lightSrc: string;
  darkSrc: string;
  alt: string;
  width: number;
  height: number;
  className?: string;
  style?: CSSProperties;
};

/**
 * Renders a single <Image> matching the active theme.
 * Defaults to the dark variant during SSR and before hydration
 * (matches ThemeProvider defaultTheme="dark").
 *
 * WHY a client component instead of rendering both images with CSS toggle:
 * The previous implementation rendered two <Image> tags (light + dark) and
 * used `display: none` to hide one. Browsers download *both* images even
 * when one is hidden, doubling edge requests for every themed image on
 * every page (logos, screenshots, artwork). On Vercel, each request counts
 * as a billed edge request. Switching to a single-image client component
 * halved the image request count across the site.
 *
 * next-themes injects a blocking <script> that sets the .dark class before
 * first paint, so there is no visible flash when the theme resolves.
 */
export function ThemedImage({
  lightSrc,
  darkSrc,
  alt,
  width,
  height,
  className,
  style,
}: ThemedImageProps) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const src = mounted && resolvedTheme === "light" ? lightSrc : darkSrc;

  return (
    <Image
      src={src}
      alt={alt}
      width={width}
      height={height}
      className={className}
      style={style}
    />
  );
}
