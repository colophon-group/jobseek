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
 * Renders both light and dark images; CSS `.dark` class on <html> toggles
 * which one is visible.  Avoids hydration mismatches because the HTML is
 * identical on server and client — no runtime theme check needed.
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
  return (
    <>
      <Image
        src={lightSrc}
        alt={alt}
        width={width}
        height={height}
        className={`themed-img-light${className ? ` ${className}` : ""}`}
        style={style}
      />
      <Image
        src={darkSrc}
        alt={alt}
        width={width}
        height={height}
        className={`themed-img-dark${className ? ` ${className}` : ""}`}
        style={style}
      />
    </>
  );
}
