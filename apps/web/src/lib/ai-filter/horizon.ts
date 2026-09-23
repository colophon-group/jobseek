/** Earliest supported posting timestamp for lazy historical evaluation. */
export const AI_FILTER_HISTORICAL_HORIZON_START =
  "2000-01-01T00:00:00.000Z";

export function aiFilterHistoricalHorizonStart(): Date {
  return new Date(AI_FILTER_HISTORICAL_HORIZON_START);
}

export function aiFilterHorizonEnd(now: Date): Date {
  return new Date(Math.floor(now.getTime() / 1_000) * 1_000 + 1_000);
}
