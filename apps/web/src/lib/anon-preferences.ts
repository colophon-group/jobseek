import "server-only";
import { cookies } from "next/headers";
import { getLanguage } from "@/lib/job-languages";

/**
 * Cookie used to persist anonymous-viewer job-language preferences.
 *
 * Authenticated users persist `jobLanguages` in `user_preferences` (DB).
 * Anonymous users have no DB row, so we mirror the same shape into a
 * cookie so `getViewerLanguages` can resolve a non-empty filter for
 * them. Without this cookie, `updatePreferences` silently no-ops for
 * anon callers (`if (!userId) return null`) and the UI toggle visibly
 * does nothing — see issue #2850.
 *
 * Cookie value is a JSON-encoded `string[]` matching the DB column
 * (`["*"]` for all-languages, `["en","de"]` for explicit). The empty
 * default `[]` is represented by deleting the cookie, so callers
 * don't need a separate "is set" probe.
 */
export const JOB_LANGUAGES_COOKIE = "JSEEK_JOB_LANGUAGES";

/** One year — matches `NEXT_LOCALE` and other persisted UX prefs. */
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

/**
 * Sanitize a freshly-decoded cookie value into the same shape stored
 * in `user_preferences.jobLanguages`. Mirrors the write-side validator
 * in `actions/preferences.ts::sanitizeJobLanguages` — the cookie is
 * untrusted client input, and these strings flow into Typesense
 * `filter_by` and Postgres array literals via raw interpolation.
 */
function sanitize(input: unknown): string[] | null {
  if (!Array.isArray(input)) return null;
  const out: string[] = [];
  for (const item of input) {
    if (typeof item !== "string") return null;
    if (item === "*" || getLanguage(item) != null) out.push(item);
  }
  return out;
}

/**
 * Read the anonymous viewer's persisted `jobLanguages` preference from
 * the request cookie. Returns `null` when:
 *
 *   - the cookie is missing,
 *   - the cookie value is not valid JSON,
 *   - the parsed value is not an array of known language codes.
 *
 * Callers that need a default (`[]`) on the missing-cookie path should
 * check for `null` and substitute `[]` themselves — keeping the two
 * states distinguishable lets `getViewerLanguages` log malformed
 * cookies without conflating them with the default case.
 */
export async function readAnonJobLanguagesCookie(): Promise<string[] | null> {
  const store = await cookies();
  const raw = store.get(JOB_LANGUAGES_COOKIE)?.value;
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Malformed cookie — treat as missing. Don't throw; the page must
    // still render, and the next legitimate write will overwrite.
    return null;
  }
  return sanitize(parsed);
}

/**
 * Persist the anonymous viewer's `jobLanguages` preference as a cookie
 * mirroring `user_preferences.jobLanguages`. Sanitizes the input first
 * so the read path can trust the cookie blindly. The empty default
 * (`[]`) is encoded as cookie deletion so a user can "reset" by
 * removing all selections.
 *
 * `path: "/"` so every page sees the cookie. `sameSite: "lax"` because
 * the value is read by GET-rendered pages (the read happens during
 * RSC payload generation, which uses GET semantics). Not `httpOnly` —
 * `secure` only when the request was over HTTPS so dev (`http://`)
 * still works without special-casing.
 */
export async function writeAnonJobLanguagesCookie(input: string[]): Promise<void> {
  const safe = sanitize(input) ?? [];
  const store = await cookies();
  if (safe.length === 0) {
    store.delete(JOB_LANGUAGES_COOKIE);
    return;
  }
  store.set(JOB_LANGUAGES_COOKIE, JSON.stringify(safe), {
    sameSite: "lax",
    maxAge: COOKIE_MAX_AGE_SECONDS,
    path: "/",
    secure: process.env.NODE_ENV === "production",
  });
}
