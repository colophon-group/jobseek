"use client";

import { useEffect } from "react";
import { isLocale } from "@/lib/i18n";

const COOKIE_NAME = "NEXT_LOCALE";

function readLocaleCookie(): string | null {
  if (typeof document === "undefined") return null;
  const parts = document.cookie ? document.cookie.split("; ") : [];
  for (const part of parts) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    if (part.slice(0, eq) !== COOKIE_NAME) continue;
    return decodeURIComponent(part.slice(eq + 1));
  }
  return null;
}

function correctLocaleIfStale() {
  if (typeof window === "undefined") return;
  const cookieLocale = readLocaleCookie();
  if (!cookieLocale || !isLocale(cookieLocale)) return;
  const segments = window.location.pathname.split("/");
  const urlLocale = segments[1];
  if (!urlLocale || !isLocale(urlLocale)) return;
  if (urlLocale === cookieLocale) return;
  segments[1] = cookieLocale;
  const newPath = segments.join("/") + window.location.search;
  // `window.location.replace` (not `router.replace`) so this component
  // reads no Next.js navigation hooks. Under cacheComponents (#2835),
  // `useRouter()`/`usePathname()` are dynamic-API reads that opt the
  // parent layout out of static rendering — mounted at the
  // `[lang]/layout.tsx` root, that taints every page in the route
  // tree and breaks the production build (#3001 P0). Browser-native
  // APIs are runtime-only and don't leak into static analysis.
  window.location.replace(newPath);
}

/**
 * Redirect any in-app navigation to the locale the user has explicitly
 * selected (via `LocaleSwitcher` in the nav or the Language section in
 * `/settings`). Both surfaces write the `NEXT_LOCALE` cookie on every
 * locale switch — this component is the read side that fixes the
 * stale-history hole described in #2988:
 *
 *   1. User opens /en/explore.
 *   2. Goes to /en/settings, picks German.
 *   3. `handleLocaleSwitch` sets `NEXT_LOCALE=de` and `router.push`es
 *      to /de/settings — that one navigation is correct.
 *   4. User clicks browser-back. Browser walks history to /en/settings
 *      → /en/explore. Without this guard, the product surface stays in
 *      English (URL `[lang]=en`) until a hard reload, even though the
 *      cookie says `de`.
 *
 * Implementation: runs once on mount + listens for `popstate` to handle
 * browser back/forward across stale history entries. SSR-safe.
 */
export function LocaleGuard() {
  useEffect(() => {
    correctLocaleIfStale();
    const onPopState = () => correctLocaleIfStale();
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  return null;
}
