"use server";

import { getSession } from "@/lib/sessionCache";
import { getPreferences } from "@/lib/actions/preferences";
import { resolveJobLanguages } from "@/lib/job-languages";

/**
 * Resolve the current viewer's effective job-language filter.
 * Anonymous viewers (no session) fall through to `[locale]` — same as
 * logged-in users whose preference is unset. Keeps
 * explore/company/watchlist pages behaving identically across auth state.
 *
 * Returns `[]` when the viewer opted into "all languages" (`"*"`).
 */
export async function getViewerLanguages(locale: string): Promise<string[]> {
  const session = await getSession();
  const prefs = session ? await getPreferences() : null;
  return resolveJobLanguages(prefs?.jobLanguages ?? [], locale);
}
