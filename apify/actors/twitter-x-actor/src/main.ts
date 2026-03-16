/**
 * @actor twitter-x-actor
 *
 * Monitors X (Twitter) for posts from CTOs, founders, and VPs of Engineering
 * that contain growth or hiring signals.
 *
 * Strategy:
 *   - Builds Twitter search queries from input keywords + @handles
 *   - Delegates scraping to the Apify managed actor `apify/twitter-scraper`
 *     (avoids maintaining our own browser automation)
 *   - Post-processes results using scorer.ts (keyword-weighted scoring)
 *   - Filters to tweets above minScore threshold before emitting Signals
 *
 * Signal type produced: `twitter`
 *
 * Input schema (actor.json):
 * {
 *   xHandles:    string[]  (e.g. ["elonmusk", "sama"] — without @)
 *   keywords:    string[]  (e.g. ["we're hiring", "just raised", "scaling"])
 *   lookbackDays: number   (default: 7)
 *   minScore:    number    (default: 5 — tweets below this are discarded)
 * }
 *
 * Note: This actor requires your Apify account to have access to `apify/twitter-scraper`.
 * The actor is paid. Check https://apify.com/apidojo/tweet-scraper for pricing.
 */

import { Actor } from 'apify';
import { createHash } from 'crypto';
import { Signal } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { pushDataWithFallback } from '../../../shared/storage';
import { scoreTweet } from './scorer';

interface TwitterActorInput {
  xHandles?: string[];
  keywords?: string[];
  lookbackDays?: number;
  minScore?: number;
}

/** Expected shape of items returned by apify/twitter-scraper */
interface TweetResult {
  id?: string;
  full_text?: string;
  text?: string;
  created_at?: string;
  user?: {
    screen_name?: string;
    name?: string;
    description?: string; // Twitter bio — used to extract company name
  };
  author?: {
    userName?: string;
    name?: string;
  };
  url?: string;
  retweet_count?: number;
  favorite_count?: number;
}

await Actor.init();

const input = (await Actor.getInput<TwitterActorInput>()) ?? {};
const {
  xHandles = [],
  keywords = ["we're hiring", 'scaling', 'just raised', 'new office', 'series b', 'series c'],
  lookbackDays = 7,
  minScore = 5,
} = input;

console.log(
  `Starting twitter-x-actor: handles=${xHandles.length}, keywords=${keywords.length}, lookbackDays=${lookbackDays}`
);

const cutoff = new Date();
cutoff.setDate(cutoff.getDate() - lookbackDays);
const cutoffStr = cutoff.toISOString().split('T')[0]; // 'YYYY-MM-DD' — Twitter `since:` operator format

// --- Build search queries ---
// Twitter's search syntax: `keyword since:YYYY-MM-DD` or `from:handle since:YYYY-MM-DD`
const searchQueries: string[] = [];

for (const keyword of keywords) {
  searchQueries.push(`${keyword} since:${cutoffStr}`);
}

for (const handle of xHandles) {
  const cleanHandle = handle.replace(/^@/, ''); // Strip leading @ if present
  searchQueries.push(`from:${cleanHandle} since:${cutoffStr}`);
}

console.log(`Built ${searchQueries.length} search queries for Twitter scraper`);

const allTweets: TweetResult[] = [];

// --- Delegate scraping to apify/twitter-scraper ---
try {
  const twitterScraperInput = {
    searchTerms: searchQueries,
    maxItems: 200,
    sort: 'Latest',
    tweetLanguage: 'en',
  };

  console.log('Calling apify/twitter-scraper...');
  const run = await Actor.call('apify/twitter-scraper', twitterScraperInput);

  if (run?.defaultDatasetId) {
    const dataset = await Actor.openDataset(run.defaultDatasetId);
    const { items } = await dataset.getData();
    allTweets.push(...(items as TweetResult[]));
    console.log(`Retrieved ${allTweets.length} tweets from Twitter scraper`);
  }
} catch (err) {
  console.error('Error calling apify/twitter-scraper:', err);
}

// --- Score and filter tweets ---
const signals: Signal[] = [];
const seenIds = new Set<string>(); // Dedup within this run

for (const tweet of allTweets) {
  const text = tweet.full_text ?? tweet.text ?? '';
  if (!text) continue;

  const { score, matchedKeywords } = scoreTweet(text);
  if (score < minScore) continue; // Below threshold, skip

  const screenName = tweet.user?.screen_name ?? tweet.author?.userName ?? 'unknown';
  const userName = tweet.user?.name ?? tweet.author?.name ?? screenName;
  const createdAt = tweet.created_at ? new Date(tweet.created_at) : new Date();

  if (createdAt < cutoff) continue; // Double-check against cutoff (Twitter search isn't always exact)

  // Infer the company from the author's profile
  const company = inferCompanyFromHandle(userName, tweet.user?.description ?? '');
  const domain = guessDomainFromCompany(company || screenName);

  const tweetDate = createdAt.toISOString().split('T')[0];
  const id = createHash('sha256')
    .update(`${screenName}:twitter:${tweetDate}:${(tweet.id ?? '').slice(0, 8)}`)
    .digest('hex')
    .slice(0, 16);

  if (seenIds.has(id)) continue;
  seenIds.add(id);

  const tweetUrl = tweet.url ?? `https://twitter.com/${screenName}/status/${tweet.id ?? ''}`;

  const signal: Signal = {
    id,
    company: company || userName,
    company_domain: domain,
    signal_type: 'twitter',
    signal_text: text.slice(0, 500), // Cap length for dataset storage
    source_url: tweetUrl,
    date: createdAt.toISOString(),
    score, // Pre-populate score from keyword scorer; orchestrator may override with Claude score
    raw: {
      tweet_id: tweet.id,
      screen_name: screenName,
      user_name: userName,
      retweet_count: tweet.retweet_count ?? 0,
      favorite_count: tweet.favorite_count ?? 0,
      matched_keywords: matchedKeywords,
      score,
    },
  };

  signals.push(signal);
}

// Sort by score descending so highest-quality signals are at the top of the dataset
signals.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));

console.log(`Processed ${signals.length} qualifying tweet signals`);

// --- Write to shared signals dataset ---
await pushDataWithFallback(signals, DATASETS.SIGNALS);

console.log(`Pushed ${signals.length} Twitter signals to dataset '${DATASETS.SIGNALS}'`);

await Actor.exit();

/**
 * Attempts to extract a company name from a Twitter user's name and bio.
 * Checks bio for patterns like "at CompanyName" or "@ CompanyName".
 * Falls back to the user's display name.
 */
function inferCompanyFromHandle(userName: string, description: string): string {
  const bioMatch = description.match(/(?:at|@)\s+([A-Z][a-zA-Z0-9\s]{2,30})/);
  if (bioMatch?.[1]) return bioMatch[1].trim();
  return userName;
}

/** Converts a display name or company name to a guessed domain slug */
function guessDomainFromCompany(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
    .slice(0, 30);
  return `${slug}.com`;
}
