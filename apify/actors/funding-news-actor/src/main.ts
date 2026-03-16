/**
 * @actor funding-news-actor
 *
 * Detects funding round signals from two sources:
 *   1. Crunchbase API  — structured data, requires API key (paid)
 *   2. RSS feeds       — TechCrunch Venture + VentureBeat Business (free, lower fidelity)
 *
 * Output: Signal[] written to the 'hiring-signals' Apify dataset.
 *
 * Signal type produced: `funding`
 *
 * Why funding signals matter:
 *   Companies that just closed a Series B/C/D typically hire aggressively in
 *   the 4–12 weeks after announcement. Reaching out in week 1–2 puts you ahead
 *   of the public job posting by months.
 *
 * Input schema (actor.json):
 * {
 *   crunchbaseApiKey:   string  (optional — skips Crunchbase if absent)
 *   minRoundAmountUsd:  number  (default: 10_000_000)
 *   roundTypes:         string[] (default: ['series_b','series_c','series_d','series_e'])
 *   lookbackDays:       number  (default: 7)
 * }
 *
 * Downstream: orchestrator-actor reads from 'hiring-signals', scores this actor's
 * output, and calls contact-finder-actor + email-drafter-actor for qualifying signals.
 */

import { Actor } from 'apify';
import { Signal } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { pushDataWithFallback } from '../../../shared/storage';
import { parseCrunchbase } from './sources/crunchbase';
import { parseRssFeeds } from './sources/rss';

interface FundingActorInput {
  crunchbaseApiKey: string;
  minRoundAmountUsd?: number;
  roundTypes?: string[];
  lookbackDays?: number;
}

await Actor.init();

const input = (await Actor.getInput<FundingActorInput>()) ?? {};
const {
  crunchbaseApiKey,
  minRoundAmountUsd = 10_000_000,
  roundTypes = ['series_b', 'series_c', 'series_d', 'series_e'],
  lookbackDays = 7,
} = input;

console.log(`Starting funding-news-actor with lookbackDays=${lookbackDays}, minRoundAmountUsd=${minRoundAmountUsd}`);

const allSignals: Signal[] = [];

// --- Source 1: Crunchbase (structured, paid API) ---
if (crunchbaseApiKey) {
  console.log('Fetching signals from Crunchbase...');
  try {
    const crunchbaseSignals = await parseCrunchbase(crunchbaseApiKey, minRoundAmountUsd, roundTypes, lookbackDays);
    console.log(`Got ${crunchbaseSignals.length} signals from Crunchbase`);
    allSignals.push(...crunchbaseSignals);
  } catch (err) {
    console.error('Error fetching from Crunchbase:', err);
  }
} else {
  console.warn('No crunchbaseApiKey provided, skipping Crunchbase source');
}

// --- Source 2: RSS feeds (free, regex-parsed) ---
console.log('Fetching signals from RSS feeds...');
try {
  const rssSignals = await parseRssFeeds(lookbackDays);
  console.log(`Got ${rssSignals.length} signals from RSS feeds`);
  allSignals.push(...rssSignals);
} catch (err) {
  console.error('Error fetching from RSS feeds:', err);
}

// --- Deduplicate by signal id ---
// Two sources can produce the same round (e.g., Crunchbase + TechCrunch article).
// The id is hash(company:funding:date) so same-day same-company rounds collapse.
const signalMap = new Map<string, Signal>();
for (const signal of allSignals) {
  if (!signalMap.has(signal.id)) {
    signalMap.set(signal.id, signal);
  }
}

const deduped = Array.from(signalMap.values());
console.log(`Total unique signals after deduplication: ${deduped.length}`);

// --- Write to shared signals dataset ---
await pushDataWithFallback(deduped, DATASETS.SIGNALS);

console.log(`Pushed ${deduped.length} signals to dataset '${DATASETS.SIGNALS}'`);

await Actor.exit();
