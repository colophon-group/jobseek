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
