"use server";

import { cookies } from "next/headers";
import { getSessionUserId } from "@/lib/sessionCache";
import { getOwnedWatchlistById } from "@/lib/services/watchlists";
import {
  WATCHLIST_SELECTION_COOKIE,
  encodeWatchlistSelection,
  isWatchlistId,
  watchlistSelectionCookieOptions,
} from "@/lib/watchlist-selection";

async function clearSelectionCookie(): Promise<void> {
  const store = await cookies();
  store.set(WATCHLIST_SELECTION_COOKIE, "", {
    ...watchlistSelectionCookieOptions,
    maxAge: 0,
  });
}

export async function selectOwnedWatchlist(
  watchlistId: string,
): Promise<{ ok: true } | { ok: false }> {
  const userId = await getSessionUserId();
  if (!userId || !isWatchlistId(watchlistId)) {
    await clearSelectionCookie();
    return { ok: false };
  }

  // The cookie is only a hint. Re-authorize the exact `(id, user_id)` pair
  // before writing it so stale, deleted, or cross-account IDs converge to the
  // same empty state without revealing which condition occurred.
  const detail = await getOwnedWatchlistById(watchlistId, userId);
  if (!detail) {
    await clearSelectionCookie();
    return { ok: false };
  }

  const store = await cookies();
  store.set(
    WATCHLIST_SELECTION_COOKIE,
    encodeWatchlistSelection(userId, detail.id),
    watchlistSelectionCookieOptions,
  );
  return { ok: true };
}

export async function clearWatchlistSelection(): Promise<void> {
  await clearSelectionCookie();
}
