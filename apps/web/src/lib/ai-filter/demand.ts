import { AI_FILTER_MAX_SEARCH_CANDIDATES } from "./search-eligibility";

/** The ordinary watchlist UI renders 20 jobs at a time. */
export const AI_FILTER_RESULT_PAGE_SIZE = 20;
/**
 * Maintain a raw-candidate runway behind the visible result cursor. Natural-
 * language requests are selective: 60 candidates often produce far less than
 * one 20-result page. A 500-candidate runway produced roughly three result
 * pages in the live internship feed while costing at most $0.035 at the
 * conservative per-job Jev estimate. Work still starts only after the user
 * opens/scrolls the narrowed surface and never crosses the 10k ceiling.
 */
export const AI_FILTER_PREFETCH_CANDIDATES = 500;

export function aiFilterDemandTarget(requestedOffset: unknown): number {
  if (
    typeof requestedOffset !== "number" ||
    !Number.isSafeInteger(requestedOffset) ||
    requestedOffset < 0 ||
    requestedOffset > AI_FILTER_MAX_SEARCH_CANDIDATES
  ) {
    throw new TypeError("AI filter demand offset is invalid");
  }
  return Math.min(
    AI_FILTER_MAX_SEARCH_CANDIDATES,
    requestedOffset + AI_FILTER_PREFETCH_CANDIDATES,
  );
}

export function aiFilterDemandIsCovered(input: {
  targetOffset: number;
  selectionOffset: number;
  scannedCount: number;
  caughtUp: boolean;
}): boolean {
  return input.caughtUp ||
    input.selectionOffset + input.scannedCount >= input.targetOffset;
}
