import { AI_FILTER_MAX_SEARCH_CANDIDATES } from "./search-eligibility";

/** The ordinary watchlist UI renders 20 jobs at a time. */
export const AI_FILTER_RESULT_PAGE_SIZE = 20;
/** Keep three result pages evaluated ahead of the user's current offset. */
export const AI_FILTER_PREFETCH_PAGES = 3;
export const AI_FILTER_PREFETCH_CANDIDATES =
  AI_FILTER_RESULT_PAGE_SIZE * AI_FILTER_PREFETCH_PAGES;

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
