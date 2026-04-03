/**
 * Typesense watchlist indexing helpers.
 *
 * All operations are fire-and-forget — they catch and log errors,
 * never blocking the user mutation. If Typesense is down, the Supabase
 * write succeeds and the periodic reconciliation job syncs later.
 *
 * Uses getWriteClient() (TYPESENSE_WRITE_KEY), NOT the search client.
 */

import { getWriteClient } from "@/lib/search/typesense-client";

export interface WatchlistDoc {
  id: string;
  slug: string;
  title: string;
  description?: string;
  owner_name: string;
  owner_username?: string;
  company_count: number;
  active_job_count: number;
  mirror_count: number;
  created_at: number; // Unix timestamp
  is_public: boolean;
}

/** Upsert a public watchlist document. */
export function upsertWatchlist(doc: WatchlistDoc): void {
  getWriteClient()
    .collections("watchlist")
    .documents()
    .upsert(doc)
    .catch((err) => {
      console.error("[typesense] failed to upsert watchlist", doc.id, err);
    });
}

/** Delete a watchlist document by ID. */
export function deleteWatchlist(watchlistId: string): void {
  getWriteClient()
    .collections("watchlist")
    .documents(watchlistId)
    .delete()
    .catch((err) => {
      // 404 is fine — the doc may not exist (e.g., was never public)
      if (err?.httpStatus === 404) return;
      console.error("[typesense] failed to delete watchlist", watchlistId, err);
    });
}

/** Update a single field on a watchlist document (partial update). */
export function updateWatchlistField(
  watchlistId: string,
  fields: Partial<WatchlistDoc>,
): void {
  getWriteClient()
    .collections("watchlist")
    .documents(watchlistId)
    .update(fields)
    .catch((err) => {
      // 404 is fine — the doc may not exist yet
      if (err?.httpStatus === 404) return;
      console.error("[typesense] failed to update watchlist", watchlistId, err);
    });
}
