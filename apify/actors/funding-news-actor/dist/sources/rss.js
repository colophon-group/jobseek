"use strict";
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
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.parseRssFeeds = parseRssFeeds;
const rss_parser_1 = __importDefault(require("rss-parser"));
const crypto_1 = require("crypto");
/** Feed definitions — add more feeds here to expand coverage */
const RSS_FEEDS = [
    {
        name: 'TechCrunch Venture',
        url: 'https://techcrunch.com/tag/venture/feed/',
    },
    {
        name: 'VentureBeat Business',
        url: 'https://venturebeat.com/category/business/feed/',
    },
];
/**
 * Regex patterns to extract funding info from article titles/descriptions.
 *
 * company patterns:  Match the company name at the start of the headline.
 * amount patterns:   Match dollar amounts like "$50M" or "$1.2 billion".
 * roundType pattern: Match round labels like "Series C", "Seed", "Series A".
 */
const FUNDING_PATTERNS = {
    company: [
        /^([A-Z][a-zA-Z0-9\s.,'&-]+?)\s+(?:raises?|secures?|lands?|closes?|announces?)/i,
        /^([A-Z][a-zA-Z0-9\s.,'&-]+?),\s+/i,
    ],
    amount: [
        /\$(\d+(?:\.\d+)?)\s*(billion|million|[BM])\b/i,
        /raises?\s+\$(\d+(?:\.\d+)?)\s*(billion|million|[BM])/i,
    ],
    roundType: [
        /(seed|pre-seed|series\s+[a-g]|series[a-g]|growth|late.stage|bridge|ipo|spac)/i,
    ],
};
/**
 * Fetches and parses funding signals from RSS feeds.
 *
 * @param lookbackDays - Articles older than this are skipped
 * @returns Array of Signal objects with signal_type = 'funding'
 */
async function parseRssFeeds(lookbackDays) {
    const parser = new rss_parser_1.default({
        customFields: {
            item: ['content', 'summary'],
        },
        timeout: 15000,
    });
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - lookbackDays);
    const signals = [];
    for (const feed of RSS_FEEDS) {
        console.log(`Fetching RSS feed: ${feed.name} (${feed.url})`);
        try {
            const result = await parser.parseURL(feed.url);
            for (const item of result.items) {
                const pubDate = item.pubDate ? new Date(item.pubDate) : null;
                if (pubDate && pubDate < cutoff)
                    continue;
                const title = item.title ?? '';
                const description = item.contentSnippet ?? item.summary ?? item.content ?? '';
                const fullText = `${title} ${description}`;
                const extracted = extractFundingInfo(fullText);
                if (!extracted)
                    continue; // Not a funding article, skip
                const { company, amountFormatted, roundType } = extracted;
                const articleDate = pubDate?.toISOString() ?? new Date().toISOString();
                const domain = guessDomainFromCompany(company);
                const signalText = [
                    `${company} announced`,
                    roundType ? `a ${roundType}` : 'a funding round',
                    amountFormatted ? `of ${amountFormatted}` : '',
                ]
                    .filter(Boolean)
                    .join(' ');
                // Same hash formula as crunchbase.ts — enables cross-source deduplication
                const id = (0, crypto_1.createHash)('sha256')
                    .update(`${company}:funding:${articleDate.split('T')[0]}`)
                    .digest('hex')
                    .slice(0, 16);
                const signal = {
                    id,
                    company,
                    company_domain: domain,
                    signal_type: 'funding',
                    signal_text: signalText,
                    source_url: item.link ?? feed.url,
                    date: articleDate,
                    raw: {
                        feed: feed.name,
                        title,
                        description: description.slice(0, 500),
                        amount_formatted: amountFormatted,
                        round_type: roundType,
                    },
                };
                signals.push(signal);
            }
        }
        catch (err) {
            console.error(`Error parsing RSS feed ${feed.name}:`, err);
        }
    }
    return signals;
}
/**
 * Attempts to extract company name, dollar amount, and round type from
 * a combined article title + description string.
 *
 * Returns null if the text doesn't appear to be a funding article (no funding verbs).
 */
function extractFundingInfo(text) {
    // Guard: must contain funding-related verbs to be worth processing
    const fundingVerbs = /\b(raises?|secures?|lands?|closes?|funding|funded|investment|raised|announced)\b/i;
    if (!fundingVerbs.test(text))
        return null;
    // --- Extract company name ---
    let company = null;
    for (const pattern of FUNDING_PATTERNS.company) {
        const match = text.match(pattern);
        if (match?.[1]) {
            company = match[1].trim().replace(/\s+/g, ' ');
            break;
        }
    }
    // Reject if no match or name is implausibly short/long
    if (!company || company.length < 2 || company.length > 60)
        return null;
    // --- Extract dollar amount ---
    let amountFormatted = null;
    for (const pattern of FUNDING_PATTERNS.amount) {
        const match = text.match(pattern);
        if (match) {
            const num = parseFloat(match[1]);
            const unit = match[2]?.toLowerCase();
            if (unit === 'billion' || unit === 'b') {
                amountFormatted = `$${num}B`;
            }
            else if (unit === 'million' || unit === 'm') {
                amountFormatted = `$${num}M`;
            }
            break;
        }
    }
    // --- Extract round type ---
    let roundType = null;
    const rtMatch = text.match(FUNDING_PATTERNS.roundType[0]);
    if (rtMatch?.[1]) {
        roundType = rtMatch[1]
            .replace(/\s+/g, ' ')
            .split(' ')
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
            .join(' ');
    }
    return { company, amountFormatted, roundType };
}
/**
 * Converts a company name to a guessed domain slug.
 * e.g. "Stripe Inc." → "stripe.com"
 * Used as a best-effort input to Hunter.io domain search.
 */
function guessDomainFromCompany(company) {
    const slug = company
        .toLowerCase()
        .replace(/[^a-z0-9]/g, '')
        .slice(0, 30);
    return `${slug}.com`;
}
//# sourceMappingURL=rss.js.map