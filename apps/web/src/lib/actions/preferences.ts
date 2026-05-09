"use server";

import { eq, sql } from "drizzle-orm";
import { headers } from "next/headers";
import { cacheLife, revalidatePath } from "next/cache";
import { db } from "@/db";
import { account, userPreferences } from "@/db/schema";
import { auth } from "@/lib/auth";
import { getSession, getSessionUserId } from "@/lib/sessionCache";
import { getLanguage } from "@/lib/job-languages";
import { writeAnonJobLanguagesCookie, readAnonJobLanguagesCookie } from "@/lib/anon-preferences";

const PASSWORD_RESET_COOLDOWN_SECONDS = 60;

/**
 * jobLanguages flows into Typesense `filter_by` strings and Postgres array
 * literals via raw interpolation, so we whitelist at the write boundary.
 * Anything not a known language code (or the "*" all-languages sentinel)
 * is silently dropped to keep the array shape predictable.
 */
function sanitizeJobLanguages(input: string[]): string[] {
  return input.filter((c) => c === "*" || getLanguage(c) != null);
}

/**
 * Routes whose `'use cache'` output depends on the viewer's job-language
 * filter (DB row for auth users, `JSEEK_JOB_LANGUAGES` cookie for anon).
 * After `updatePreferences` mutates `jobLanguages`, every cached layer
 * across these routes needs to be invalidated — otherwise the user
 * navigates back to a stale prerender that predates the cookie write
 * (#2916). Stays in sync with the read sites enumerated in PR #2914.
 */
const JOB_LANGUAGE_DEPENDENT_PATHS = [
  "/[lang]/(app)/explore",
  "/[lang]/(app)/[userSlug]/[watchlistSlug]",
  "/[lang]/(app)/company/[slug]",
] as const;

/**
 * Invalidate every `'use cache'` page that reads the viewer's
 * `jobLanguages` so the next render reflects the just-persisted value.
 *
 * `revalidatePath` is the right primitive here even though the data
 * dependency comes via cookie/DB rather than a `cacheTag`: it evicts
 * the per-region cache entry for the route and forces a fresh server
 * render on the next request. The caller (a client component in
 * /settings) ALSO calls `router.refresh()` to flush the client-side
 * router cache that holds a snapshot of /explore in memory across
 * back-nav (#2916). Both layers must be cleared — the per-region
 * cache is the source for the RSC payload server-side, the router
 * cache is the source the browser uses on back-nav.
 *
 * Failures are swallowed because a successful preference write is the
 * load-bearing operation; a revalidation hiccup must not 500 the form
 * submit. The user's next hard reload would self-heal anyway.
 */
function invalidateJobLanguageDependentPages(): void {
  for (const path of JOB_LANGUAGE_DEPENDENT_PATHS) {
    try {
      // Match the existing convention from
      // `app/api/web/companies/request/[run_id]/status/route.ts` —
      // single-arg call invalidates that page's cache entries across
      // every locale. Passing the second `"page"` arg is also valid
      // but the unspecified default is what the rest of the codebase
      // uses for App-Router page paths.
      revalidatePath(path);
    } catch (err) {
      console.warn("[preferences] revalidatePath failed for", path, err);
    }
  }
}

export async function getPreferences() {
  const session = await getSession();
  if (!session) return null;

  const [row] = await db
    .select()
    .from(userPreferences)
    .where(eq(userPreferences.userId, session.user.id))
    .limit(1);

  return row ?? null;
}

/**
 * Resolve the viewer's currently-saved `jobLanguages` preference,
 * unifying the authenticated (DB-row) and anonymous (cookie) paths.
 *
 * Returns the same shape the rest of the codebase expects:
 *   - `[]` when nothing is set (UI treats as "default = locale only")
 *   - `["*"]` when the viewer opted into "all languages"
 *   - explicit codes otherwise (e.g. `["en","de"]`)
 *
 * Used by the settings page so the toggle reflects the persisted state
 * for anon viewers — without this, the toggle would visibly forget the
 * selection on the next render even though the cookie is set. See
 * #2850 + `anon-preferences.ts`.
 */
export async function getViewerJobLanguages(): Promise<string[]> {
  const prefs = await getPreferences();
  if (prefs) return prefs.jobLanguages ?? [];
  return (await readAnonJobLanguagesCookie()) ?? [];
}

export async function updatePreferences(
  data: {
    theme?: "light" | "dark";
    locale?: "en" | "de" | "fr" | "it";
    jobLanguages?: string[];
    displayCurrency?: string;
    salaryPeriod?: string | null;
    cookieConsent?: boolean;
    dismissBanner?: string;
    themeUpdatedAt?: string;
    localeUpdatedAt?: string;
  },
) {
  const userId = await getSessionUserId();
  if (!userId) {
    // Anonymous users have no DB row, but we still persist
    // `jobLanguages` so the explore/watchlist filter actually applies
    // on subsequent renders. Other prefs (theme, locale, currency,
    // dismissBanner, …) are persisted client-side via `localPrefs` /
    // `next-themes` / Lingui's locale prefix, so we only mirror the
    // server-resolved field. See issue #2850 + `anon-preferences.ts`.
    if (data.jobLanguages !== undefined) {
      await writeAnonJobLanguagesCookie(data.jobLanguages);
      // Cookie write happened — flush every page whose `'use cache'`
      // output depends on the cookie. Without this the back-nav from
      // /settings to /explore renders the prerender that predates the
      // toggle; the user only sees the new filter after a hard reload
      // (#2916).
      invalidateJobLanguageDependentPages();
    }
    return null;
  }

  const [existing] = await db
    .select()
    .from(userPreferences)
    .where(eq(userPreferences.userId, userId))
    .limit(1);

  // For updates (existing row), enforce "only update if newer" per field
  if (existing) {
    const set: Record<string, unknown> = {
      updatedAt: new Date(),
    };

    if (data.cookieConsent !== undefined) {
      set.cookieConsent = data.cookieConsent;
    }

    if (data.dismissBanner) {
      const current = existing.dismissedBanners ?? [];
      if (!current.includes(data.dismissBanner)) {
        set.dismissedBanners = [...current, data.dismissBanner];
      }
    }

    if (data.jobLanguages !== undefined) {
      set.jobLanguages = sanitizeJobLanguages(data.jobLanguages);
    }

    if (data.displayCurrency !== undefined) {
      set.displayCurrency = data.displayCurrency;
    }

    if (data.salaryPeriod !== undefined) {
      set.salaryPeriod = data.salaryPeriod;
    }

    // Theme: only update if incoming timestamp >= existing, or no existing timestamp
    if (data.theme !== undefined) {
      const incomingTs = data.themeUpdatedAt ? new Date(data.themeUpdatedAt) : null;
      const existingTs = existing.themeUpdatedAt;
      if (!existingTs || !incomingTs || incomingTs >= existingTs) {
        set.theme = data.theme;
        set.themeUpdatedAt = incomingTs ?? new Date();
      }
    }

    // Locale: only update if incoming timestamp >= existing, or no existing timestamp
    if (data.locale !== undefined) {
      const incomingTs = data.localeUpdatedAt ? new Date(data.localeUpdatedAt) : null;
      const existingTs = existing.localeUpdatedAt;
      if (!existingTs || !incomingTs || incomingTs >= existingTs) {
        set.locale = data.locale;
        set.localeUpdatedAt = incomingTs ?? new Date();
      }
    }

    const [row] = await db
      .update(userPreferences)
      .set(set)
      .where(eq(userPreferences.userId, userId))
      .returning();

    // `jobLanguages` was part of this write — flush every cached
    // page whose render reads it. Mirrors the anon path (#2916). The
    // mutation for other fields (theme/locale/currency) is observed
    // by the caller via `router.refresh()` and doesn't need a
    // server-side `revalidatePath` because none of those flow into
    // the explore/watchlist `'use cache'` outputs.
    if (data.jobLanguages !== undefined) {
      invalidateJobLanguageDependentPages();
    }

    return row;
  }

  // Insert (new row): always write everything
  const [row] = await db
    .insert(userPreferences)
    .values({
      userId,
      theme: data.theme ?? "light",
      locale: data.locale ?? "en",
      jobLanguages: data.jobLanguages ? sanitizeJobLanguages(data.jobLanguages) : [],
      displayCurrency: data.displayCurrency ?? "EUR",
      cookieConsent: data.cookieConsent ?? false,
      themeUpdatedAt: data.themeUpdatedAt ? new Date(data.themeUpdatedAt) : new Date(),
      localeUpdatedAt: data.localeUpdatedAt ? new Date(data.localeUpdatedAt) : new Date(),
    })
    .onConflictDoUpdate({
      target: userPreferences.userId,
      set: {
        updatedAt: new Date(),
      },
    })
    .returning();

  // First-write inserts always materialise `jobLanguages` (defaults to
  // `[]` when omitted), which still affects every dependent page if
  // the caller passed a non-default — invalidate when the field was
  // explicitly set so the upsert path stays consistent with the
  // update path above (#2916).
  if (data.jobLanguages !== undefined) {
    invalidateJobLanguageDependentPages();
  }

  return row;
}

export async function getPasswordResetCooldown(): Promise<number> {
  const session = await getSession();
  if (!session) return 0;

  const [row] = await db
    .select({ lastPasswordResetAt: userPreferences.lastPasswordResetAt })
    .from(userPreferences)
    .where(eq(userPreferences.userId, session.user.id))
    .limit(1);

  if (!row?.lastPasswordResetAt) return 0;

  const elapsed = Math.floor((Date.now() - row.lastPasswordResetAt.getTime()) / 1000);
  return Math.max(0, PASSWORD_RESET_COOLDOWN_SECONDS - elapsed);
}

export async function recordPasswordResetRequest(): Promise<{ error?: string; cooldown?: number }> {
  const session = await getSession();
  if (!session) return { error: "Not authenticated" };

  const remaining = await getPasswordResetCooldown();
  if (remaining > 0) {
    return { cooldown: remaining };
  }

  await db
    .insert(userPreferences)
    .values({
      userId: session.user.id,
      theme: "light",
      locale: "en",
      cookieConsent: false,
      lastPasswordResetAt: new Date(),
    })
    .onConflictDoUpdate({
      target: userPreferences.userId,
      set: {
        lastPasswordResetAt: new Date(),
        updatedAt: new Date(),
      },
    });

  return {};
}

export async function setPassword(newPassword: string): Promise<{ error?: string }> {
  const session = await getSession();
  if (!session) return { error: "Not authenticated" };

  try {
    await auth.api.setPassword({
      body: { newPassword },
      headers: await headers(),
    });
    return {};
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : "Failed to set password";
    return { error: message };
  }
}

/**
 * Returns everything the account settings page needs in a single call.
 * Called from the page server component to avoid client-side fetches.
 */
export async function getAccountPageData() {
  const session = await getSession();
  if (!session) return null;

  const accounts = await db
    .select({ providerId: account.providerId, accountId: account.accountId })
    .from(account)
    .where(eq(account.userId, session.user.id));

  return {
    accounts: accounts.map((a) => ({ providerId: a.providerId, accountId: a.accountId })),
    hasPassword: accounts.some((a) => a.providerId === "credential"),
    username: ((session.user as Record<string, unknown>).username as string | null) ?? "",
  };
}

export interface AvailableLanguage {
  code: string;
  count: number;
}

/**
 * Returns distinct language codes from active job postings with counts, sorted by count desc.
 * Per-region in-memory `'use cache'` (cacheLife('hours')); migrated from
 * Redis-backed `cached(..., { ttl: 3600 })` in #2884 (bucket 5). Build ID
 * is part of the cache key, so each deploy re-fetches.
 */
export async function getAvailableJobLanguages(): Promise<AvailableLanguage[]> {
  "use cache";
  cacheLife("hours");
  const rows = await db.execute<{ [key: string]: unknown; locale: string; cnt: number }>(sql`
    SELECT locale, COUNT(*)::int AS cnt
    FROM (
      SELECT unnest(locales) AS locale
      FROM job_posting
      WHERE is_active = true AND array_length(locales, 1) > 0
    ) sub
    GROUP BY locale
    ORDER BY cnt DESC
  `);
  return (rows as unknown as { locale: string; cnt: number }[]).map((r) => ({
    code: r.locale,
    count: r.cnt,
  }));
}
