"use client";

import { useParams } from "next/navigation";

/**
 * Returns a function that prefixes a path with the current locale.
 * Handles hash-only paths (e.g. "/#features" → "/de/#features")
 * and skips external/protocol URLs.
 */
export function useLocalePath() {
  const params = useParams();
  const lang = (params.lang as string) ?? "en";

  return function localePath(href: string): string {
    // Skip external links, mailto:, etc.
    if (href.startsWith("http") || href.startsWith("mailto:")) return href;
    // Already prefixed
    if (href.startsWith(`/${lang}/`) || href === `/${lang}`) return href;
    // Prefix with locale
    if (href.startsWith("/")) return `/${lang}${href}`;
    return `/${lang}/${href}`;
  };
}
