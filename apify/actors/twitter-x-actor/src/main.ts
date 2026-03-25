/**
 * @actor twitter-x-actor
 *
 * Monitors X (Twitter) for growth/hiring posts from CTOs, founders, and VPs.
 * Delegates scraping to `apify/twitter-scraper`, then scores with keyword heuristics.
 * Signal type: `twitter`
 */

import { Actor } from 'apify';
import { runSignalActor } from '../../../shared/signalActor';
import { signalId } from '../../../shared/id';
import { guessDomain } from '../../../shared/utils';
import type { Signal } from '../../../shared/types';
import { scoreTweet } from './scorer';

interface TwitterActorInput {
  xHandles?: string[];
  keywords?: string[];
  lookbackDays?: number;
  minScore?: number;
}

interface TweetResult {
  id?: string;
  full_text?: string;
  text?: string;
  created_at?: string;
  user?: { screen_name?: string; name?: string; description?: string };
  author?: { userName?: string; name?: string };
  url?: string;
  retweet_count?: number;
  favorite_count?: number;
}

runSignalActor<TwitterActorInput>(async (input) => {
  const {
    xHandles = [],
    keywords = ["we're hiring", 'scaling', 'just raised', 'new office', 'series b', 'series c'],
    lookbackDays = 7,
    minScore = 5,
  } = input;

  console.log(`twitter-x-actor: handles=${xHandles.length}, keywords=${keywords.length}, lookbackDays=${lookbackDays}`);

  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);
  const cutoffStr = cutoff.toISOString().split('T')[0];

  // Build search queries
  const searchQueries: string[] = [
    ...keywords.map((kw) => `${kw} since:${cutoffStr}`),
    ...xHandles.map((h) => `from:${h.replace(/^@/, '')} since:${cutoffStr}`),
  ];

  console.log(`Built ${searchQueries.length} search queries`);

  // Delegate scraping
  let allTweets: TweetResult[] = [];
  try {
    const run = await Actor.call('apify/twitter-scraper', {
      searchTerms: searchQueries,
      maxItems: 200,
      sort: 'Latest',
      tweetLanguage: 'en',
    });

    if (run?.defaultDatasetId) {
      const dataset = await Actor.openDataset(run.defaultDatasetId);
      const { items } = await dataset.getData();
      allTweets = items as TweetResult[];
      console.log(`Retrieved ${allTweets.length} tweets`);
    }
  } catch (err) {
    console.error('Error calling apify/twitter-scraper:', err);
  }

  // Score and filter
  const signals: Signal[] = [];
  const seenIds = new Set<string>();

  for (const tweet of allTweets) {
    const text = tweet.full_text ?? tweet.text ?? '';
    if (!text) continue;

    const { score, matchedKeywords } = scoreTweet(text);
    if (score < minScore) continue;

    const screenName = tweet.user?.screen_name ?? tweet.author?.userName ?? 'unknown';
    const userName = tweet.user?.name ?? tweet.author?.name ?? screenName;
    const createdAt = tweet.created_at ? new Date(tweet.created_at) : new Date();
    if (createdAt < cutoff) continue;

    const company = inferCompany(userName, tweet.user?.description ?? '');
    const tweetDate = createdAt.toISOString().split('T')[0];
    const id = signalId(screenName, 'twitter', tweetDate, (tweet.id ?? '').slice(0, 8));

    if (seenIds.has(id)) continue;
    seenIds.add(id);

    signals.push({
      id,
      company: company || userName,
      company_domain: guessDomain(company || screenName),
      signal_type: 'twitter',
      signal_text: text.slice(0, 500),
      source_url: tweet.url ?? `https://twitter.com/${screenName}/status/${tweet.id ?? ''}`,
      date: createdAt.toISOString(),
      score,
      raw: {
        tweet_id: tweet.id,
        screen_name: screenName,
        user_name: userName,
        retweet_count: tweet.retweet_count ?? 0,
        favorite_count: tweet.favorite_count ?? 0,
        matched_keywords: matchedKeywords,
        score,
      },
    });
  }

  signals.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  return signals;
});

function inferCompany(userName: string, description: string): string {
  const bioMatch = description.match(/(?:at|@)\s+([A-Z][a-zA-Z0-9\s]{2,30})/);
  if (bioMatch?.[1]) return bioMatch[1].trim();
  return userName;
}
