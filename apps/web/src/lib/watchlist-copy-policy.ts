/**
 * Source-side authorization for watchlist copies.
 *
 * Keep this separate from destination capacity: a source decision says only
 * whether the caller may clone this row. The destination-owner limit remains
 * an independent, transaction-scoped policy in `watchlist-limit.ts`.
 *
 * Only `owned` is live today. The other kinds are deliberately represented so
 * a future grant/share/template implementation has to add real evidence here
 * instead of falling back to visibility or bypassing destination capacity.
 */
export const WATCHLIST_COPY_SOURCE_KINDS = [
  "owned",
  "grant",
  "share",
  "template",
] as const;

export type WatchlistCopySourceKind =
  (typeof WATCHLIST_COPY_SOURCE_KINDS)[number];

export type WatchlistCopyAuthorization = {
  sourceKind: WatchlistCopySourceKind;
};

export function authorizeWatchlistCopySource(
  source: { userId: string },
  destinationUserId: string,
  requestedSourceKind: WatchlistCopySourceKind = "owned",
): WatchlistCopyAuthorization | null {
  switch (requestedSourceKind) {
    case "owned":
      return source.userId === destinationUserId
        ? { sourceKind: "owned" }
        : null;
    case "grant":
    case "share":
    case "template":
      // Dormant until the corresponding server-verified authorization source
      // exists. In particular, `is_public` is intentionally not evidence.
      return null;
  }
}
