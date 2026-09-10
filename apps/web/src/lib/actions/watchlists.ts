"use server";

// Thin `"use server"` wrapper around the pure service tier from
// `@/lib/services/watchlists`. The service module holds the implementation
// (no `"use server"` directive) and is the boundary used by public REST
// route handlers under `apps/web/app/api/v1/*` -- see issue #3332.
//
// Why declared async wrappers instead of `export { foo } from "..."`?
// Next.js / Turbopack processes `"use server"` files by scanning for
// declared async functions. Pure value re-exports can produce zero action
// exports at build time, so each wrapper delegates explicitly.
//
// Type re-exports are safe: they are erased at compile time and do not go
// through the server-action transform.

import * as service from "@/lib/services/watchlists";

export async function createWatchlist(
  ...args: Parameters<typeof service.createWatchlist>
): ReturnType<typeof service.createWatchlist> {
  return service.createWatchlist(...args);
}

export async function createWatchlistFromHandoff(
  ...args: Parameters<typeof service.createWatchlistFromHandoff>
): ReturnType<typeof service.createWatchlistFromHandoff> {
  return service.createWatchlistFromHandoff(...args);
}

export async function updateWatchlist(
  ...args: Parameters<typeof service.updateWatchlist>
): ReturnType<typeof service.updateWatchlist> {
  return service.updateWatchlist(...args);
}

export async function shareWatchlist(
  ...args: Parameters<typeof service.shareWatchlist>
): ReturnType<typeof service.shareWatchlist> {
  return service.shareWatchlist(...args);
}

export async function deleteWatchlist(
  ...args: Parameters<typeof service.deleteWatchlist>
): ReturnType<typeof service.deleteWatchlist> {
  return service.deleteWatchlist(...args);
}

export async function copyWatchlist(
  ...args: Parameters<typeof service.copyWatchlist>
): ReturnType<typeof service.copyWatchlist> {
  return service.copyWatchlist(...args);
}

export async function copySharedWatchlist(
  ...args: Parameters<typeof service.copySharedWatchlist>
): ReturnType<typeof service.copySharedWatchlist> {
  return service.copySharedWatchlist(...args);
}

export async function toggleWatchlistAlerts(
  ...args: Parameters<typeof service.toggleWatchlistAlerts>
): ReturnType<typeof service.toggleWatchlistAlerts> {
  return service.toggleWatchlistAlerts(...args);
}

export async function getUserWatchlistsWithLimit(
  ...args: Parameters<typeof service.getUserWatchlistsWithLimit>
): ReturnType<typeof service.getUserWatchlistsWithLimit> {
  return service.getUserWatchlistsWithLimit(...args);
}

export async function getUserWatchlists(
  ...args: Parameters<typeof service.getUserWatchlists>
): ReturnType<typeof service.getUserWatchlists> {
  return service.getUserWatchlists(...args);
}

export async function getUserWatchlistCounts(
  ...args: Parameters<typeof service.getUserWatchlistCounts>
): ReturnType<typeof service.getUserWatchlistCounts> {
  return service.getUserWatchlistCounts(...args);
}

export async function addCompanyToWatchlist(
  ...args: Parameters<typeof service.addCompanyToWatchlist>
): ReturnType<typeof service.addCompanyToWatchlist> {
  return service.addCompanyToWatchlist(...args);
}

export async function clearWatchlistCompanies(
  ...args: Parameters<typeof service.clearWatchlistCompanies>
): ReturnType<typeof service.clearWatchlistCompanies> {
  return service.clearWatchlistCompanies(...args);
}

export async function removeCompanyFromWatchlist(
  ...args: Parameters<typeof service.removeCompanyFromWatchlist>
): ReturnType<typeof service.removeCompanyFromWatchlist> {
  return service.removeCompanyFromWatchlist(...args);
}

export type {
  WatchlistFilters,
  WatchlistSummary,
  UserWatchlistOverview,
  UserWatchlistActivityPreview,
  WatchlistDetail,
  WatchlistViewDetail,
  WatchlistPostingEntry,
} from "@/lib/services/watchlists";
