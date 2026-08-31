/**
 * Source-side authorization for watchlist copies.
 *
 * Keep this separate from destination capacity: future share grants and
 * curated templates can extend this policy without bypassing the universal
 * destination-owner limit enforced by the creation transaction.
 */
export function canCopyWatchlistSource(source: {
  userId: string;
  isPublic: boolean;
}, destinationUserId: string): boolean {
  return source.userId === destinationUserId || source.isPublic;
}
