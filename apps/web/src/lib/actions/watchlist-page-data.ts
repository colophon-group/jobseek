"use server";

import type { WatchlistPageData } from "@/lib/services/watchlist-page-data";

/**
 * Fail-closed tombstone for browser bundles holding the retired public
 * snapshot action ID. Shared watchlists now resolve by unlisted UUID through
 * the server-only loader; this legacy slug action must never return stored
 * public data.
 */
export async function fetchPublicWatchlistPageData(
  _params: unknown,
): Promise<WatchlistPageData | null> {
  return null;
}

/** Fail-closed tombstone for the former personalized slug-page action ID. */
export async function fetchWatchlistPageData(_params: {
  userSlug: string;
  watchlistSlug: string;
  locale: string;
}): Promise<WatchlistPageData | null> {
  return null;
}

export type { WatchlistPageData } from "@/lib/services/watchlist-page-data";
