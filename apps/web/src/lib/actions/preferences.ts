"use server";

import { eq, sql } from "drizzle-orm";
import { headers } from "next/headers";
import { cacheLife, revalidatePath, updateTag } from "next/cache";
import { after } from "next/server";
import { db } from "@/db";
import { account, user, userPreferences } from "@/db/schema";
import { auth } from "@/lib/auth";
import {
  getSession,
  getSessionUserId,
  invalidateAllUserSessionCacheEntries,
} from "@/lib/sessionCache";
import { getLanguage } from "@/lib/job-languages";
import { writeAnonJobLanguagesCookie, readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { getSearchClient } from "@/lib/search/typesense-client";
import { invalidate as invalidateRedis } from "@/lib/cache";
import { watchlistCacheTag } from "@/lib/cache-tags";
import { updateWatchlistField as tsUpdateWatchlistField } from "@/lib/search/typesense-watchlist";
import { isReservedUsername } from "@/lib/username";
import { isTrivialWatchlist } from "@/lib/watchlist-utils";
import { notifyIndexNow } from "@/lib/indexnow";
import type { WatchlistFilters } from "@/lib/actions/watchlists";

const PASSWORD_RESET_COOLDOWN_SECONDS = 60;

export type PreferencesActionErrorCode =
  | "not_authenticated"
  | "password_set_failed"
  | "username_length"
  | "username_invalid_characters"
  | "username_reserved"
  | "username_update_failed"
  | "user_not_found";

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

/**
 * Null out the current viewer's `user.image` column.
 *
 * Called from `UserAvatar`'s `onError` handler when the stored OAuth
 * avatar URL fails to load. LinkedIn (`media.licdn.com`) signs profile
 * photos with a time-limited `e=<epoch>` param; once the signature
 * expires the URL 410s permanently — the image will never come back, so
 * every subsequent page load was firing a doomed network request and
 * flashing a broken-image icon. After this action returns, the next
 * session refresh resolves `user.image` to `null` and the UI cleanly
 * falls back to the initials placeholder. See issue #3035.
 *
 * Self-healing by construction: the only render path that calls this
 * action is `<img>` with a non-null `src`. Once the row is nulled, no
 * future render emits the `<img>`, so the action can never re-fire from
 * the same user-agent on the same row. (A subsequent OAuth re-link
 * would write a fresh `image` value via Better Auth; that's the only
 * way back to a non-null state.)
 *
 * Best-effort: returns silently on auth-missing / write failure rather
 * than throwing. The caller is a fire-and-forget client handler; a
 * thrown promise from a server action turns into a console error with
 * no UX recovery.
 */
export async function clearStoredUserImage(): Promise<void> {
  const userId = await getSessionUserId();
  if (!userId) return;

  try {
    await db
      .update(user)
      .set({ image: null, updatedAt: new Date() })
      .where(eq(user.id, userId));
  } catch (err) {
    console.warn("[clearStoredUserImage] db write failed", err);
    return;
  }

  // Bust the Redis-cached `getSession()` blob across every device the
  // user is logged in on, so the next bootstrap fetch returns
  // `image: null` instead of the just-cleared URL (TTL is 5 min
  // otherwise). Mirrors the pattern from `renameUsername`. Failures
  // are logged inside the helper — the DB write is the load-bearing
  // operation and we already succeeded.
  await invalidateAllUserSessionCacheEntries(userId);
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

/**
 * Record a password-reset request for the current viewer.
 *
 * Combines the per-user 60-second cooldown check and the timestamp
 * write into a single atomic SQL statement so two concurrent callers
 * (most commonly two browser tabs of the same user double-clicking
 * "Forgot password?") can't both pass a stale SELECT and fire two
 * reset emails (#3165).
 *
 * Shape: `INSERT … ON CONFLICT (user_id) DO UPDATE … WHERE
 * <cooldown-elapsed>`. The `ON CONFLICT … WHERE` predicate runs
 * atomically with the upsert: when the cooldown has not elapsed the
 * update is skipped and `RETURNING` emits zero rows. Postgres serialises
 * concurrent upserts on the same conflict target, so exactly one of two
 * racing callers sees `updated=1` and the other sees `updated=0`.
 *
 * Return shape:
 *   - `{}` — fresh write committed; caller should fire the reset email
 *   - `{ cooldown }` — cooldown still active; caller should NOT fire
 *     the reset email and should show the remaining seconds
 *   - `{ error }` — not authenticated
 *
 * `getPasswordResetCooldown()` stays as a read-only helper for any
 * future SSR initial-state hookup; it is not used to gate the write
 * here, because doing so would re-open the same TOCTOU window the
 * single-statement upsert exists to close.
 */
export async function recordPasswordResetRequest(): Promise<{ error?: PreferencesActionErrorCode; cooldown?: number }> {
  const session = await getSession();
  if (!session) return { error: "not_authenticated" };

  // Single-statement upsert + cooldown gate. The `WHERE` clause on the
  // `DO UPDATE` branch is evaluated atomically inside the upsert; if it
  // fails the row is left untouched AND no `RETURNING` row is emitted.
  // The INSERT branch (first-ever request) always emits a row, because
  // a fresh INSERT can't fail the WHERE check.
  const rows = await db.execute<{ updated: number }>(sql`
    INSERT INTO user_preferences (user_id, theme, locale, cookie_consent, last_password_reset_at)
    VALUES (${session.user.id}, 'light', 'en', false, now())
    ON CONFLICT (user_id) DO UPDATE
      SET last_password_reset_at = now(), updated_at = now()
      WHERE user_preferences.last_password_reset_at IS NULL
         OR user_preferences.last_password_reset_at
              < now() - make_interval(secs => ${PASSWORD_RESET_COOLDOWN_SECONDS})
    RETURNING 1 AS updated
  `);

  // `db.execute` returns a postgres-js RowList that's array-like; the
  // mocks in tests return plain arrays. `.length` covers both shapes.
  const updated = (rows as unknown as ArrayLike<unknown>).length > 0;
  if (updated) return {};

  // Cooldown still active — another concurrent caller (or a recent
  // sequential one) won the race. Tell the caller how long is left so
  // the UI can show the same cooldown banner it would have shown if
  // the user had hit the button alone after a successful request.
  const remaining = await getPasswordResetCooldown();
  // Clamp to a minimum of 1 second so the UI always renders the
  // cooldown branch even if `now()` has just crossed the boundary
  // between the upsert's evaluation and the read below.
  return { cooldown: Math.max(1, remaining) };
}

export async function setPassword(newPassword: string): Promise<{ error?: PreferencesActionErrorCode }> {
  const session = await getSession();
  if (!session) return { error: "not_authenticated" };

  try {
    await auth.api.setPassword({
      body: { newPassword },
      headers: await headers(),
    });
    return {};
  } catch {
    return { error: "password_set_failed" };
  }
}

const USERNAME_RE = /^[a-z0-9][a-z0-9-]*[a-z0-9]$/;

/**
 * Rename the current user's username and fan out every cache invalidation
 * the rename invalidates.
 *
 * Wraps Better Auth's `/update-user` (which only rewrites the DB row +
 * re-signs the session cookie) with the application-level invalidations
 * the platform also needs:
 *
 *  - The Redis-cached `getSession()` blob (keyed by the signed cookie
 *    value, which Better Auth does NOT rotate on rename) keeps the old
 *    `user.username` for up to 5 min — busted for every active session
 *    token so other devices see the new username promptly.
 *  - The watchlist detail `'use cache'` entries are tagged by
 *    `watchlistCacheTag(userSlug, watchlistSlug)`. Once the user-row
 *    flips both `username` and `display_username` to the new value
 *    (Better Auth's username plugin mirrors `body.username` →
 *    `body.displayUsername` in its before-hook for `/update-user`), the
 *    OLD slug no longer matches the DB and every visit under the old
 *    URL 404s. The cache tags + Redis `public-watchlist:` entries keyed
 *    on the old slug are busted here so the 404 surfaces immediately
 *    instead of being papered over by a stale render.
 *  - Typesense `watchlist` docs carry a denormalised `owner_username`
 *    that's only rewritten when the watchlist itself is mutated. Each
 *    of the user's existing docs is patched here so public watchlist
 *    search reflects the new owner slug without waiting for the next
 *    per-watchlist mutation.
 *  - The sitemap Redis cache holds rendered watchlist URLs for 1h; the
 *    cache is dropped so the next crawl pulls the new slugs.
 *
 * Returns `{ error }` with a human-readable message on validation or
 * uniqueness failures, `{}` on success. No-ops when the requested name
 * already matches the stored value.
 */
export async function renameUsername(
  newUsername: string,
): Promise<{ error?: PreferencesActionErrorCode }> {
  const currentSession = await getSession();
  if (!currentSession) return { error: "not_authenticated" };
  const userId = currentSession.user.id;

  // Mirror the client-side validation in `UsernameSection` so a stray
  // direct call to this server action can't bypass it.
  const normalized = newUsername.toLowerCase().trim();
  if (normalized.length < 3 || normalized.length > 30) {
    return { error: "username_length" };
  }
  if (!USERNAME_RE.test(normalized)) {
    return { error: "username_invalid_characters" };
  }
  if (isReservedUsername(normalized)) {
    return { error: "username_reserved" };
  }

  // Snapshot the pre-rename state BEFORE handing off to Better Auth.
  // We need both the OLD slug variants (for the cache-tag bust) and the
  // full set of the user's watchlist ids + the session-token list (for
  // the Typesense + multi-device session-cache bust) at a point in time
  // where the user row still holds the OLD username.
  const [oldUserRow] = await db
    .select({
      username: user.username,
      displayUsername: user.displayUsername,
    })
    .from(user)
    .where(eq(user.id, userId))
    .limit(1);
  if (!oldUserRow) return { error: "user_not_found" };

  if (oldUserRow.username === normalized) return {}; // no-op rename

  // Snapshot every watchlist the user owns with the fields the fanout
  // needs: `slug` (cache-tag bust + IndexNow URL), `isPublic` +
  // `filters` + `company_count` (qualifying check for IndexNow — only
  // public, non-trivial watchlists are indexed by sitemap.ts and
  // therefore worth pinging on rename). Single SQL with a correlated
  // subquery so we don't pay N+1 round-trips inside the rename critical
  // path; the same idea as `_getOwnerInfo` in `watchlists.ts`.
  const watchlistRows = await db.execute<{
    [key: string]: unknown;
    id: string;
    slug: string;
    is_public: boolean;
    filters: WatchlistFilters | null;
    company_count: number;
  }>(sql`
    SELECT
      w.id, w.slug, w.is_public, w.filters,
      COALESCE(
        (SELECT count(*)::int FROM watchlist_company wc WHERE wc.watchlist_id = w.id),
        0
      ) AS company_count
    FROM watchlist w
    WHERE w.user_id = ${userId}
  `);
  type WatchlistSnapshot = {
    id: string;
    slug: string;
    isPublic: boolean;
    filters: WatchlistFilters;
    companyCount: number;
  };
  const userWatchlists: WatchlistSnapshot[] = (
    watchlistRows as unknown as Array<{
      id: string;
      slug: string;
      is_public: boolean;
      filters: WatchlistFilters | null;
      company_count: number;
    }>
  ).map((r) => ({
    id: r.id,
    slug: r.slug,
    isPublic: r.is_public,
    filters: (r.filters ?? {}) as WatchlistFilters,
    companyCount: r.company_count,
  }));

  // Delegate the actual DB write + cookie re-sign to Better Auth so the
  // username plugin's uniqueness check and the `usernameValidator`
  // configured in `auth.ts` run authoritatively.
  try {
    await auth.api.updateUser({
      body: { username: normalized },
      headers: await headers(),
    });
  } catch {
    return { error: "username_update_failed" };
  }

  // ── Fanout below this point — best-effort, never rollback the rename ──

  const oldUserSlugs = new Set<string>();
  if (oldUserRow.username) oldUserSlugs.add(oldUserRow.username);
  if (oldUserRow.displayUsername) oldUserSlugs.add(oldUserRow.displayUsername);

  // Watchlist detail caches are keyed by whichever userSlug variant the
  // visitor hit (`username` or `display_username` — see the route
  // resolver in `getWatchlistByUserAndSlug`). Bust both for every
  // owned watchlist so the old `/{userSlug}/{watchlistSlug}` cached
  // render is evicted and the next visit fetches the fresh DB row
  // (which now 404s — engines should learn it).
  for (const oldSlug of oldUserSlugs) {
    for (const wl of userWatchlists) {
      updateTag(watchlistCacheTag(oldSlug, wl.slug));
      try {
        await invalidateRedis(`public-watchlist:${oldSlug}:${wl.slug}`);
      } catch (err) {
        console.error(
          "[renameUsername] redis public-watchlist invalidate failed",
          err,
        );
      }
    }
  }

  // Refresh Typesense `owner_username` for each existing watchlist doc
  // so the public watchlist search shows the new `@username` without
  // waiting for the next per-watchlist mutation. `is_featured` is also
  // derived from `username.toLowerCase() === "colophongroup"` in the
  // mutation hooks (`watchlists.ts:158,273,471`) — a rename TO or AWAY
  // from that handle has to refresh the flag or it stays stale until
  // the next mutation. Partial update is a no-op if the doc doesn't
  // exist (private / trivial watchlists). `tsUpdateWatchlistField` is
  // fire-and-forget; errors are caught + logged inside the helper.
  const newIsFeatured = normalized === "colophongroup";
  for (const wl of userWatchlists) {
    tsUpdateWatchlistField(wl.id, {
      owner_username: normalized,
      is_featured: newIsFeatured,
    });
  }

  // Sitemap entries embed `display_username ?? username` per row and
  // are cached for `SITEMAP_TTL_SECONDS` (1h). Drop the cache so the
  // next sitemap request reflects the new slugs.
  try {
    await invalidateRedis("sitemap:watchlists");
  } catch (err) {
    console.error("[renameUsername] sitemap invalidate failed", err);
  }

  // Bust the Redis-cached `getSession()` blob for every device the user
  // is logged in on. The cookie Better Auth re-signs in
  // `setSessionCookie` keeps the same `session.token` and thus the same
  // signed cookie value, so `session:<signed-value>` in Redis still
  // holds the pre-rename user payload until the 5-min TTL expires.
  // SCAN-filtered eviction is the simplest cross-device bust we can do
  // without duplicating better-call's signing code or maintaining a
  // userId → token index.
  await invalidateAllUserSessionCacheEntries(userId);

  // Ping IndexNow for every qualifying watchlist's NEW + OLD URLs so
  // engines re-crawl the old slug (which now 404s) and discover the
  // new slug. Mirrors the qualifying predicate used by other watchlist
  // mutations (`watchlists.ts:154,291,457`) and the sitemap filter
  // (`sitemap.ts` — only public, non-trivial watchlists are indexed).
  // Runs in `after()` so the response returns before the outbound
  // HTTP call to the IndexNow endpoint. Failures are logged, never
  // surfaced — IndexNow is best-effort SEO hygiene.
  after(async () => {
    const urls: string[] = [];
    for (const wl of userWatchlists) {
      if (!wl.isPublic) continue;
      if (isTrivialWatchlist(wl.filters, wl.companyCount)) continue;
      urls.push(`/${normalized}/${wl.slug}`);
      for (const oldSlug of oldUserSlugs) {
        urls.push(`/${oldSlug}/${wl.slug}`);
      }
    }
    if (urls.length === 0) return;
    try {
      await notifyIndexNow(urls);
    } catch (err) {
      console.error("[renameUsername] indexnow failed", err);
    }
  });

  return {};
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
 *
 * Reads from Typesense (`job_posting` collection has `locales` as a faceted
 * field). The previous Postgres implementation `unnest`ed `locales` over all
 * active rows on every cold cache miss — at ~760k active postings that took
 * ~4s on Supabase and dominated the /settings cold load (#2918). The
 * Typesense facet variant returns the same shape in ~0.6s.
 *
 * Filters out the `_none` sentinel (rows with no detected locale; written
 * by the exporter to keep `locales` non-empty for Typesense — see
 * `apps/crawler/src/typesense_schema.py`).
 *
 * Per-region in-memory `'use cache'` (cacheLife('hours')) keeps repeat
 * loads sub-millisecond; build ID is part of the cache key, so each deploy
 * re-fetches. The fallback path returns `[]` uncached so a Typesense blip
 * doesn't poison the cache for an hour. Migrated from Redis-backed
 * `cached(..., { ttl: 3600 })` in #2884 (bucket 5).
 */
async function _fetchAvailableJobLanguages(): Promise<AvailableLanguage[]> {
  "use cache";
  cacheLife("hours");
  const client = getSearchClient();
  const result = await client
    .collections("job_posting")
    .documents()
    .search({
      q: "*",
      filter_by: "is_active:=true",
      facet_by: "locales",
      // Typesense returns up to ~32 distinct values by default; bump well
      // past the ~30 codes currently in production so we don't truncate.
      max_facet_values: 100,
      per_page: 0,
    });
  const counts = result.facet_counts?.[0]?.counts ?? [];
  return counts
    .filter((c) => c.value !== "_none")
    .map((c) => ({ code: c.value, count: c.count }));
}

export async function getAvailableJobLanguages(): Promise<AvailableLanguage[]> {
  try {
    return await _fetchAvailableJobLanguages();
  } catch {
    // Typesense unreachable — return empty list rather than blocking the
    // settings page render. The UI falls back to UI-locale-only filter.
    return [];
  }
}
