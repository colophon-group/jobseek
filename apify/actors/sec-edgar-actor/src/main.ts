/**
 * @actor sec-edgar-actor
 *
 * Parses SEC 10-K and 10-Q filings for hiring and growth signals using EDGAR's
 * free full-text search API. No API key required.
 *
 * Two search modes (see parser.ts for details):
 *   1. Phrase search — scans all recent filings for HIRING_PHRASES
 *   2. Company search — looks up recent filings for specific named companies
 *
 * Signal type produced: `sec_filing`
 *
 * Why SEC filings?
 *   Companies are legally required to disclose material business plans.
 *   Phrases like "we plan to hire" or "expanding our team" in a 10-K mean
 *   headcount growth is actually funded and approved — not just PR talk.
 *
 * Input schema (actor.json):
 * {
 *   companies:    string[]  (optional — filters results; empty = return all filers)
 *   lookbackDays: number    (default: 30 — filings take longer to appear than news)
 * }
 *
 * Rate limiting:
 *   parser.ts adds a 300ms sleep between each EDGAR query phrase.
 *   Do not reduce this — SEC's fair-use policy requires polite crawlers.
 */

import { Actor } from 'apify';
import { Signal } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { pushDataWithFallback } from '../../../shared/storage';
import { parseEdgarFilings } from './parser';

interface SecEdgarActorInput {
  companies?: string[];
  lookbackDays?: number;
}

await Actor.init();

const input = (await Actor.getInput<SecEdgarActorInput>()) ?? {};
const { companies = [], lookbackDays = 30 } = input;

console.log(
  `Starting sec-edgar-actor: companies=${companies.length > 0 ? companies.join(', ') : 'all'}, lookbackDays=${lookbackDays}`
);

let signals: Signal[] = [];

try {
  signals = await parseEdgarFilings(companies, lookbackDays);
  console.log(`Parsed ${signals.length} SEC filing signals`);
} catch (err) {
  console.error('Fatal error parsing EDGAR filings:', err);
  await Actor.exit({ exit: false });
  process.exit(1);
}

// Push to dataset
await pushDataWithFallback(signals, DATASETS.SIGNALS);

console.log(`Pushed ${signals.length} SEC signals to dataset '${DATASETS.SIGNALS}'`);

await Actor.exit();
