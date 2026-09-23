import { ANON_MAX_WATCHLIST_POSTINGS } from "@/lib/search/constants";

/** Apply the same anonymous cap to API and server-rendered shared feeds. */
export function sharedAcceptedPageBounds(input: {
  anonymous: boolean;
  offset: number;
  limit: number;
}):
  | { exhausted: true; nextOffset: number }
  | { exhausted: false; limit: number; maxOffset: number | null } {
  if (!Number.isSafeInteger(input.offset) || input.offset < 0 ||
      !Number.isSafeInteger(input.limit) || input.limit < 1 || input.limit > 100) {
    throw new TypeError("Invalid pagination");
  }
  if (input.anonymous && input.offset >= ANON_MAX_WATCHLIST_POSTINGS) {
    return { exhausted: true, nextOffset: ANON_MAX_WATCHLIST_POSTINGS };
  }
  return {
    exhausted: false,
    limit: input.anonymous
      ? Math.min(input.limit, Math.max(0, ANON_MAX_WATCHLIST_POSTINGS - input.offset))
      : input.limit,
    maxOffset: input.anonymous ? ANON_MAX_WATCHLIST_POSTINGS : null,
  };
}

/** Only the posting is public to a link viewer, even for accepted decisions. */
export function projectSharedAcceptedPage<TPosting>(
  page: {
    decisions: ReadonlyArray<{ posting: TPosting }>;
    total?: number;
    nextOffset: number;
    hasMore: boolean;
  },
  maxOffset: number | null,
) {
  return {
    decisions: page.decisions.map(({ posting }) => ({ posting })),
    total: page.total,
    nextOffset: page.nextOffset,
    hasMore: page.hasMore && (maxOffset === null || page.nextOffset < maxOffset),
  };
}
