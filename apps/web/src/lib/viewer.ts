"use server";

import { getSession } from "@/lib/sessionCache";
import { getPreferences } from "@/lib/actions/preferences";
import { resolveJobLanguages } from "@/lib/job-languages";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";

/**
 * Resolve the current viewer's effective job-language filter.
 * Authenticated viewers read `jobLanguages` from `user_preferences`.
 * Anonymous viewers read it from the `JSEEK_JOB_LANGUAGES` cookie
 * (written by `updatePreferences` for the same shape — see issue
 * #2850 + `anon-preferences.ts`); the previous behaviour was to fall
 * straight through to `[locale]`, which silently dropped any anon
 * toggle on the client.
 *
 * In both cases, an unset preference falls through to `[locale]` via
 * `resolveJobLanguages`. Returns `[]` when the viewer opted into "all
 * languages" (`"*"`).
 */
export async function getViewerLanguages(locale: string): Promise<string[]> {
  const session = await getSession();
  let stored: string[];
  if (session) {
    const prefs = await getPreferences();
    stored = prefs?.jobLanguages ?? [];
  } else {
    stored = (await readAnonJobLanguagesCookie()) ?? [];
  }
  return resolveJobLanguages(stored, locale);
}
