"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { isLocale } from "@/lib/i18n";

const COOKIE_NAME = "NEXT_LOCALE";

function readLocaleCookie(): string | null {
  if (typeof document === "undefined") return null;
  // Linear scan is fine — cookies are O(handful) on this site.
  const parts = document.cookie ? document.cookie.split("; ") : [];
  for (const part of parts) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    if (part.slice(0, eq) !== COOKIE_NAME) continue;
    const value = decodeURIComponent(part.slice(eq + 1));
    return value;
  }
  return null;
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
 * On every pathname change, if `NEXT_LOCALE` is set and disagrees with
 * the URL `[lang]` segment, `router.replace` to the same path with the
 * correct locale prefix. `replace` (not `push`) so going back doesn't
 * re-walk to the wrong-locale entry.
 *
 * Caveats:
 * - SSR-safe: all reads gated on `typeof document !== "undefined"`.
 * - Idempotent: only fires when both URL and cookie are valid locales
 *   and they differ — never recursive.
 * - Search params preserved across the redirect; the user's filter
 *   state in /explore must survive.
 * - We deliberately do *not* read `localPrefs.locale` (localStorage):
 *   the canonical signal is the cookie, which both switchers set.
 *   Mixing localStorage in here would re-create the divergence the
 *   cookie write was designed to close.
 */
export function LocaleGuard() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    const cookieLocale = readLocaleCookie();
    if (!cookieLocale || !isLocale(cookieLocale)) return;

    const segments = pathname.split("/");
    const urlLocale = segments[1];
    if (!urlLocale || !isLocale(urlLocale)) return;
    if (urlLocale === cookieLocale) return;

    segments[1] = cookieLocale;
    const newPath = segments.join("/");
    // Read query string from `window.location` rather than `useSearchParams`
    // so this client component does not opt the parent layout out of
    // static rendering under cacheComponents (#2835). The effect runs
    // only on the client, so the browser global is always available.
    const qs = typeof window !== "undefined" ? window.location.search : "";
    router.replace(qs ? `${newPath}${qs}` : newPath);
  }, [pathname, router]);

  return null;
}
