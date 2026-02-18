import Image from "next/image";
import type { CSSProperties } from "react";
import styles from "./ThemedImage.module.css";

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
 * Renders both light and dark images; CSS toggles visibility based on
 * the `.dark` class on <html>. No client JS or React context needed.
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
        className={`${styles.light} ${className ?? ""}`}
        style={style}
      />
      <Image
        src={darkSrc}
        alt=""
        width={width}
        height={height}
        className={`${styles.dark} ${className ?? ""}`}
        style={style}
        aria-hidden
      />
    </>
  );
}
