export const VIEWER_TIME_ZONE_COOKIE = "JSEEK_VIEWER_TIME_ZONE";

const VIEWER_TIME_ZONE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;
const IANA_TIME_ZONE_RE = /^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/;

/**
 * Validate an IANA timezone before it reaches Postgres or becomes a trusted
 * server-render input. The shape check keeps the value bounded and the Intl
 * check rejects syntactically plausible but unknown zones such as
 * `Mars/Olympus`.
 */
export function isValidViewerTimeZone(value: unknown): value is string {
  if (typeof value !== "string" || !IANA_TIME_ZONE_RE.test(value)) return false;

  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format();
    return true;
  } catch {
    return false;
  }
}

export function normalizeViewerTimeZone(value: unknown): string {
  return isValidViewerTimeZone(value) ? value : "UTC";
}

/** Resolve the current browser's IANA timezone, falling back to UTC. */
export function getViewerTz(): string {
  if (typeof window === "undefined") return "UTC";
  try {
    return normalizeViewerTimeZone(
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    );
  } catch {
    return "UTC";
  }
}

/**
 * Maintain the non-sensitive viewer-timezone cookie used by server-rendered
 * stats. This is deliberately a direct browser cookie write, not a Server
 * Action: ordinary app mounts must not create an uncached POST.
 */
export function persistViewerTimeZoneCookie(): string {
  const timeZone = getViewerTz();
  if (typeof document === "undefined") return timeZone;

  const encoded = encodeURIComponent(timeZone);
  const current = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${VIEWER_TIME_ZONE_COOKIE}=`))
    ?.slice(VIEWER_TIME_ZONE_COOKIE.length + 1);

  if (current === encoded) return timeZone;

  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${VIEWER_TIME_ZONE_COOKIE}=${encoded}; Path=/; Max-Age=${VIEWER_TIME_ZONE_MAX_AGE_SECONDS}; SameSite=Lax${secure}`;
  return timeZone;
}
