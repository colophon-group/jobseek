/**
 * Resolve the IANA time-zone name of the current browser viewer.
 *
 * Falls back to `"UTC"` when:
 *  - called during SSR (no Intl runtime context guarantee on Edge)
 *  - `Intl.DateTimeFormat().resolvedOptions().timeZone` throws or
 *    returns a falsy value (very old browsers, locked-down embedded
 *    WebViews, exotic regex shenanigans, etc.)
 *
 * The fallback matches the server-side default in
 * `getStats({ tz })`, so the worst case is pre-#3199 behaviour
 * (UTC-bucketed days) instead of a crash.
 */
export function getViewerTz(): string {
  if (typeof window === "undefined") return "UTC";
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz || "UTC";
  } catch {
    return "UTC";
  }
}
