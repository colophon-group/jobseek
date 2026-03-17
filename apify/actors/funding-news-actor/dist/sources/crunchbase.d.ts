/**
 * @module funding-news-actor/sources/crunchbase
 *
 * Fetches recent funding rounds from the Crunchbase API v4.
 *
 * API used: POST https://api.crunchbase.com/api/v4/searches/funding_rounds
 * Docs: https://data.crunchbase.com/docs/using-the-api
 *
 * Authentication: API key passed as query param `user_key`.
 * Pagination: cursor-based via `after_id` field in the request body.
 *
 * The function maps Crunchbase funding round entities to the shared Signal interface.
 * Domain is derived as a best-effort guess from the org's Crunchbase permalink slug.
 */
import { Signal } from '../../../../shared/types';
/**
 * Fetches funding rounds from Crunchbase that match the given filters.
 *
 * @param apiKey       - Crunchbase API key (user_key)
 * @param minAmount    - Minimum round size in USD (e.g. 10_000_000 for $10M)
 * @param roundTypes   - Array of Crunchbase investment_type slugs, e.g. ['series_b', 'series_c']
 * @param lookbackDays - How many days back to search (filters on `announced_on`)
 * @returns Array of Signal objects with signal_type = 'funding'
 */
export declare function parseCrunchbase(apiKey: string, minAmount: number, roundTypes: string[], lookbackDays: number): Promise<Signal[]>;
//# sourceMappingURL=crunchbase.d.ts.map