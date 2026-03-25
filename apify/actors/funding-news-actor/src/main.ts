/**
 * @actor funding-news-actor
 *
 * Detects funding round signals from Crunchbase (paid API) and RSS feeds (free).
 * Signal type: `funding`
 */

import { runSignalActor } from '../../../shared/signalActor';
import type { Signal } from '../../../shared/types';
import { parseCrunchbase } from './sources/crunchbase';
import { parseRssFeeds } from './sources/rss';

interface FundingActorInput {
  crunchbaseApiKey?: string;
  minRoundAmountUsd?: number;
  roundTypes?: string[];
  lookbackDays?: number;
  fundingCategories?: string[];
}

runSignalActor<FundingActorInput>(async (input) => {
  const {
    crunchbaseApiKey,
    minRoundAmountUsd = 1_000_000,
    roundTypes = ['seed', 'pre_seed', 'series_a', 'series_b', 'series_c', 'series_d', 'series_e'],
    lookbackDays = 14,
    fundingCategories,
  } = input;

  console.log(`funding-news-actor: lookbackDays=${lookbackDays}, minAmount=${minRoundAmountUsd}`);

  const signals: Signal[] = [];

  if (crunchbaseApiKey) {
    try {
      const cb = await parseCrunchbase(crunchbaseApiKey, minRoundAmountUsd, roundTypes, lookbackDays, fundingCategories);
      console.log(`Got ${cb.length} signals from Crunchbase`);
      signals.push(...cb);
    } catch (err) {
      console.error('Error fetching from Crunchbase:', err);
    }
  } else {
    console.warn('No crunchbaseApiKey, skipping Crunchbase source');
  }

  try {
    const rss = await parseRssFeeds(lookbackDays);
    console.log(`Got ${rss.length} signals from RSS feeds`);
    signals.push(...rss);
  } catch (err) {
    console.error('Error fetching from RSS feeds:', err);
  }

  return signals;
});
