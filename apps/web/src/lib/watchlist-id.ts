const WATCHLIST_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isWatchlistId(value: string): boolean {
  return WATCHLIST_ID_PATTERN.test(value);
}
