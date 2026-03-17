/**
 * @module funding-news-actor/sources/rss
 *
 * Parses funding news from free RSS feeds using regex extraction.
 * This is a lower-fidelity alternative to Crunchbase — no API key required,
 * but company name / amount / round type are extracted via heuristic patterns.
 *
 * Feeds monitored:
 *   - TechCrunch Venture: https://techcrunch.com/tag/venture/feed/
 *   - VentureBeat Business: https://venturebeat.com/category/business/feed/
 *
 * Signal quality:
 *   Crunchbase signals are preferred when both sources detect the same round.
 *   RSS signals are deduplicated against Crunchbase by id (company:funding:date hash).
 *   The RSS extractor skips articles that don't contain funding-related verbs.
 */
import { Signal } from '../../../../shared/types';
/**
 * Fetches and parses funding signals from RSS feeds.
 *
 * @param lookbackDays - Articles older than this are skipped
 * @returns Array of Signal objects with signal_type = 'funding'
 */
export declare function parseRssFeeds(lookbackDays: number): Promise<Signal[]>;
//# sourceMappingURL=rss.d.ts.map