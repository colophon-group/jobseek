"use server";

import {
  getPublicWatchlistByUserAndSlug,
  getWatchlistByUserAndSlug,
} from "@/lib/services/watchlists";
import { getSession } from "@/lib/sessionCache";
import { getUserPlan, PLAN_LIMITS, canCreateWatchlist } from "@/lib/plans";
import { getPreferences } from "@/lib/actions/preferences";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { buildWatchlistPageData } from "@/lib/services/watchlist-page-data";
import type { WatchlistPageData } from "@/lib/services/watchlist-page-data";

/**
 * Compatibility boundary for clients holding the former public-snapshot
 * action ID. The old action trusted a serialized `detail` object. Keep the
 * export name, but require slugs and re-resolve them authoritatively so a
 * replay cannot manufacture a Typesense-backed route.
 */
export async function fetchPublicWatchlistPageData(
  params: unknown,
): Promise<WatchlistPageData | null> {
  if (
    typeof params !== "object" ||
    params === null ||
    !("userSlug" in params) ||
    !("watchlistSlug" in params) ||
    !("locale" in params) ||
    typeof params.userSlug !== "string" ||
    typeof params.watchlistSlug !== "string" ||
    typeof params.locale !== "string"
  ) {
    return null;
  }

  const detail = await getPublicWatchlistByUserAndSlug(
    params.userSlug,
    params.watchlistSlug,
  );
  if (!detail) return null;

  return buildWatchlistPageData({
    detail,
    locale: params.locale,
    isOwner: false,
    isPaidPlan: false,
    limitReached: true,
    jobLanguages: [],
    publicSnapshot: true,
  });
}

export async function fetchWatchlistPageData(params: {
  userSlug: string;
  watchlistSlug: string;
  locale: string;
}): Promise<WatchlistPageData | null> {
  const { userSlug, watchlistSlug, locale } = params;

  // Resolve the URL through authoritative Postgres state before any search
  // call. Missing/private slugs cannot reach Typesense through a stale or
  // replayed Server Action reference (#7487).
  const detail = await getWatchlistByUserAndSlug(userSlug, watchlistSlug);
  if (!detail) return null;

  const session = await getSession();
  const isOwner = session?.user?.id === detail.owner.id;
  const [plan, limit, prefs, anonJobLangs] = await Promise.all([
    session ? getUserPlan(session.user.id) : ("free" as const),
    session ? canCreateWatchlist(session.user.id) : { allowed: false, current: 0, max: 0 },
    session ? getPreferences() : Promise.resolve(null),
    session ? Promise.resolve(null) : readAnonJobLanguagesCookie(),
  ]);

  return buildWatchlistPageData({
    detail,
    isOwner,
    isPaidPlan: PLAN_LIMITS[plan].canReceiveAlerts,
    limitReached: !limit.allowed,
    locale,
    jobLanguages: prefs?.jobLanguages ?? anonJobLangs ?? [],
    publicSnapshot: false,
  });
}

export type { WatchlistPageData } from "@/lib/services/watchlist-page-data";
