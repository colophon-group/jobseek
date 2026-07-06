/**
 * Name of the non-httpOnly "hint" cookie that mirrors the presence of a
 * Better Auth session cookie. It carries no security meaning — the real
 * session_token is still httpOnly and Secure — but lets client code
 * skip network round trips for anonymous users by reading
 * `document.cookie`. See `docs/edge-requests.md` and issue #2246.
 */
export const LOGGED_IN_COOKIE = "logged_in";

/**
 * Name of the non-httpOnly cookie that persists anonymous-viewer
 * `jobLanguages` preferences. The same cookie is the canonical source
 * of truth on the server (read by `viewer.ts::getViewerLanguages` and
 * `explore-page-data.ts::fetchExplorePageData`); the client only needs to know
 * whether it's set so it can decide to re-fetch the personalised
 * `ExploreData` instead of using the anon-default static prerender
 * (#2850). MUST stay in sync with `lib/anon-preferences.ts`.
 */
export const JOB_LANGUAGES_COOKIE = "JSEEK_JOB_LANGUAGES";

/**
 * Parse a raw Cookie-header-style string (either `document.cookie` or a
 * server `Cookie` request header) and return whether `name` is present
 * as an actual cookie name — not a substring of another name.
 *
 * Accepts missing / whitespace / trailing-semicolon inputs. Refuses to
 * match values; the caller only cares that the cookie exists.
 */
export function hasCookieNamed(cookieHeader: string, name: string): boolean {
  if (!cookieHeader) return false;
  // Cookies are separated by `;`. Each entry is `name=value` with
  // optional leading whitespace. A valid cookie name contains no `=`
  // and no whitespace (per RFC 6265), so a trimmed segment matching
  // `name=...` or `name=` or exactly `name` proves existence.
  for (const raw of cookieHeader.split(";")) {
    const part = raw.trim();
    if (part === name) return true;
    if (part.startsWith(`${name}=`)) return true;
  }
  return false;
}

/**
 * Client-only: does the current browser have the `logged_in` hint cookie?
 * Returns `false` on the server (no `document`) so callers get the safe
 * "assume anonymous" default during SSR.
 */
export function hasLoggedInHint(): boolean {
  if (typeof document === "undefined") return false;
  return hasCookieNamed(document.cookie, LOGGED_IN_COOKIE);
}

/**
 * Client-only: does the current browser have a stored anon
 * `jobLanguages` cookie? Used by ``ExploreContent`` (and its peers) to
 * decide whether to refetch the personalised ``ExploreData`` even when
 * the viewer is anonymous and the URL has no filter searchParams.
 * Without this, the static anon-default prerender would render with
 * `[locale]` filters regardless of the cookie state. See #2850.
 */
export function hasAnonJobLanguagesHint(): boolean {
  if (typeof document === "undefined") return false;
  return hasCookieNamed(document.cookie, JOB_LANGUAGES_COOKIE);
}

/**
 * Clear the `logged_in` hint cookie from the client side. Used to
 * self-heal a stale hint when the server tells us the session has
 * actually expired (e.g. `fetchAppBootstrap()` returns `{user: null}`
 * while the hint was present).
 */
export function clearLoggedInHint(): void {
  if (typeof document === "undefined") return;
  // Max-Age=0 + Path=/ matches the attributes the server sets, so the
  // UA removes the cookie. `Secure` is not strictly required to clear,
  // but matching server behavior avoids creating a "second" cookie.
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${LOGGED_IN_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax${secure}`;
}
