import "server-only";
import { getCurrencyRates } from "./search";
import { resolveLocationSlugs } from "./locations";
import { resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "./taxonomy";
import { compileWatchlistMatcherSources as compile, matchCompiledWatchlistsInWindow } from "@/lib/search/watchlist-matcher";
import type { WatchlistMatcherSource } from "@/lib/watchlist-matcher-contract";
export { readWatchlistCandidates, readWatchlistCandidatesByIds, matchCompiledWatchlistsInWindow } from "@/lib/search/watchlist-matcher";
export type { CompiledWatchlistFilter, WatchlistWindowMatchResult } from "@/lib/search/watchlist-matcher";

/** Web callers retain their existing tagged Next.js taxonomy/currency caches. */
export function compileWatchlistMatcherSources(sources: readonly WatchlistMatcherSource[]) {
  return compile(sources, { getCurrencyRates, resolveLocationSlugs,
    resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs });
}

export async function matchWatchlistsInWindow(params: {
  watchlists: readonly WatchlistMatcherSource[];
  windowStart: Date;
  windowEnd: Date;
  limitPerWatchlist: number;
  requireStableOrder?: boolean;
  abortSignal?: AbortSignal;
}) {
  return matchCompiledWatchlistsInWindow({ ...params,
    watchlists: await compileWatchlistMatcherSources(params.watchlists) });
}
