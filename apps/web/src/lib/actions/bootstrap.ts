"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { withDbRetry } from "@/lib/db-retry";
import { getSession } from "@/lib/sessionCache";
import type { SavedJobStatus } from "@/lib/actions/saved-jobs";

export type SessionUser = {
  id: string;
  email: string;
  name: string;
  image?: string | null;
  emailVerified: boolean;
  username?: string | null;
  displayUsername?: string | null;
};

export type AppPreferences = {
  theme?: "light" | "dark";
  themeUpdatedAt?: Date | null;
  locale?: string;
  localeUpdatedAt?: Date | null;
  cookieConsent?: boolean;
  displayCurrency?: string;
  salaryPeriod?: string | null;
  dismissedBanners?: string[];
  jobLanguages?: string[];
};

export type AppBootstrapData = {
  user: SessionUser | null;
  prefs: AppPreferences | null;
  savedStatuses: SavedJobStatus[];
  starredIds: string[];
};

/**
 * Single Postgres round-trip that returns everything `AppBootstrapProvider`
 * needs after the session lookup. Replaces the previous parallel fan-out of
 * `getPreferences`, `getSavedJobStatuses`, and `getStarredCompanyIds` —
 * `Promise.all` was three round-trips on a cold pool, this is one. See
 * issue #2643.
 *
 * The shape mirrors the public actions so existing call sites stay
 * unchanged. JSON aggregation is wrapped in `coalesce(..., '[]'::json)` so
 * an empty bookmark/starred set returns `[]` instead of `null`.
 */
async function _fetchBootstrapForUser(userId: string): Promise<{
  prefs: AppPreferences | null;
  savedStatuses: SavedJobStatus[];
  starredIds: string[];
}> {
  type Row = {
    prefs: AppPreferences | null;
    saved_statuses: SavedJobStatus[];
    starred_ids: { company_id: string }[];
  };

  const rows = await withDbRetry(
    () =>
      db.execute<Row & Record<string, unknown>>(sql`
        SELECT
          (SELECT row_to_json(p) FROM (
            SELECT
              theme,
              theme_updated_at AS "themeUpdatedAt",
              locale,
              locale_updated_at AS "localeUpdatedAt",
              cookie_consent AS "cookieConsent",
              display_currency AS "displayCurrency",
              salary_period AS "salaryPeriod",
              dismissed_banners AS "dismissedBanners",
              job_languages AS "jobLanguages"
            FROM user_preferences
            WHERE user_id = ${userId}
            LIMIT 1
          ) p) AS prefs,
          (SELECT coalesce(json_agg(s), '[]'::json) FROM (
            SELECT
              job_posting_id AS "postingId",
              id AS "savedJobId",
              status
            FROM saved_job
            WHERE user_id = ${userId}
          ) s) AS saved_statuses,
          (SELECT coalesce(json_agg(c), '[]'::json) FROM (
            SELECT company_id FROM followed_company WHERE user_id = ${userId}
          ) c) AS starred_ids
      `),
    { label: "appBootstrap" },
  );

  const row = (rows as unknown as Row[])[0];
  if (!row) return { prefs: null, savedStatuses: [], starredIds: [] };

  const prefs = row.prefs
    ? {
        ...row.prefs,
        // Postgres returns timestamp columns as ISO strings inside json — restore
        // Date instances so downstream type checks (DB-row shape) keep working.
        themeUpdatedAt:
          row.prefs.themeUpdatedAt != null
            ? new Date(row.prefs.themeUpdatedAt as unknown as string)
            : null,
        localeUpdatedAt:
          row.prefs.localeUpdatedAt != null
            ? new Date(row.prefs.localeUpdatedAt as unknown as string)
            : null,
      }
    : null;

  return {
    prefs,
    savedStatuses: row.saved_statuses ?? [],
    starredIds: (row.starred_ids ?? []).map((c) => c.company_id),
  };
}

export async function fetchAppBootstrap(): Promise<AppBootstrapData> {
  const session = await getSession();
  if (!session) {
    return { user: null, prefs: null, savedStatuses: [], starredIds: [] };
  }

  const { prefs, savedStatuses, starredIds } = await _fetchBootstrapForUser(
    session.user.id,
  );

  return {
    user: session.user as SessionUser,
    prefs,
    savedStatuses,
    starredIds,
  };
}
