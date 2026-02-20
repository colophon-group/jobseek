"use client";

import { useContext } from "react";
import Image from "next/image";
import type { CSSProperties } from "react";
import { ThemeContext } from "@/components/ThemeProvider";

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
 * Renders only the image matching the current theme.
 * Theme is known from the first render (cookie-based), so no flash occurs.
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
  const { mode } = useContext(ThemeContext);
  const src = mode === "dark" ? darkSrc : lightSrc;

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
