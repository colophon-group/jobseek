import { getLanguage } from "@/lib/job-languages";

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
 * `viewer.ts::getViewerLanguages`); Explore and company browser loaders parse
 * the same bounded value directly so their personalized result refresh does
 * not require a page-data Server Action (#2850, #8257, #8259). MUST stay in
 * sync with `lib/anon-preferences.ts`.
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

const MAX_JOB_LANGUAGES_COOKIE_LENGTH = 1024;
const MAX_JOB_LANGUAGE_PREFERENCES = 32;

/** Read one cookie value from a Cookie-header-style string. */
export function readCookieValue(cookieHeader: string, name: string): string | null {
  if (!cookieHeader) return null;
  for (const raw of cookieHeader.split(";")) {
    const part = raw.trim();
    const separator = part.indexOf("=");
    if (separator < 0 || part.slice(0, separator) !== name) continue;
    const value = part.slice(separator + 1);
    try {
      return decodeURIComponent(value);
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Parse the client-readable anonymous job-language preference without a
 * Server Action. The cookie remains untrusted: bound its size/count and accept
 * only the same known language codes as the server-side reader.
 */
export function readAnonJobLanguagesPreference(
  cookieHeader = typeof document === "undefined" ? "" : document.cookie,
): string[] | null {
  const raw = readCookieValue(cookieHeader, JOB_LANGUAGES_COOKIE);
  if (!raw || raw.length > MAX_JOB_LANGUAGES_COOKIE_LENGTH) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed) || parsed.length > MAX_JOB_LANGUAGE_PREFERENCES) {
    return null;
  }

  const values: string[] = [];
  const seen = new Set<string>();
  for (const item of parsed) {
    if (typeof item !== "string") return null;
    if (item !== "*" && getLanguage(item) == null) return null;
    if (seen.has(item)) continue;
    seen.add(item);
    values.push(item);
  }
  return values;
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
