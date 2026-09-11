/** Pagination limits for unauthenticated users. */
export const ANON_MAX_COMPANIES = 15; // 1.5 pages of PAGE_SIZE=10
export const ANON_MAX_POSTINGS = 40; // 2 pages of PAGE_SIZE=20 (company detail page)
export const ANON_MAX_CARD_POSTINGS = 20; // 10 initial + 1 batch (company card in search)
export const ANON_MAX_WATCHLIST_POSTINGS = 20; // 1 page of 20 (watchlist detail page)

/**
 * Watchlist company-id batch size for Typesense queries. Watchlists tracking
 * more companies use URL-safe browser batches with bounded concurrency, then
 * merge the independently sorted posting pages into one global result window.
 */
export const COMPANY_BATCH_SIZE = 100;
