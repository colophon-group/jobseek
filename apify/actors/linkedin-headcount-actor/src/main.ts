/**
 * @actor linkedin-headcount-actor
 *
 * Tracks LinkedIn company headcount over time and emits a Signal when
 * a company's employee count grows by more than a configurable threshold.
 *
 * Mechanism:
 *   - Delegates LinkedIn scraping to `apify/linkedin-company-scraper`
 *   - Stores headcount snapshots per company in Apify Key-Value Store
 *     ('linkedin-headcount-snapshots') using snapshot.ts
 *   - On each run, loads the previous snapshot and computes deltaPct
 *   - Emits a `headcount` Signal only when deltaPct ≥ minHeadcountDeltaPct AND delta > 0
 *   - Always saves the latest snapshot for next-run comparison
 *
 * Signal type produced: `headcount`
 *
 * First run behavior:
 *   No previous snapshot exists → no signal emitted → baseline is recorded.
 *   Signals only appear from the second run onwards.
 *
 * Input schema (actor.json):
 * {
 *   companyUrls:           string[]  (LinkedIn company page URLs)
 *   minHeadcountDeltaPct:  number    (default: 10 — triggers at ≥10% growth)
 * }
 *
 * Requires: Apify account with access to `apify/linkedin-company-scraper` (paid actor).
 */

import { Actor, KeyValueStore } from 'apify';
import { createHash } from 'crypto';
import { Signal } from '../../../shared/types';
import { DATASETS } from '../../../shared/constants';
import { openKeyValueStoreWithFallback, pushDataWithFallback } from '../../../shared/storage';
import { loadSnapshot, saveSnapshot, HeadcountSnapshot } from './snapshot';

interface LinkedinHeadcountInput {
  companyUrls?: string[];
  minHeadcountDeltaPct?: number;
}

interface LinkedinCompanyResult {
  name?: string;
  companyName?: string;
  url?: string;
  linkedInUrl?: string;
  employeeCount?: number;
  staffCount?: number;
  numberOfEmployees?: number;
  locations?: Array<{ city?: string; country?: string; geographicArea?: string }>;
  websiteUrl?: string;
  website?: string;
}

await Actor.init();

const input = (await Actor.getInput<LinkedinHeadcountInput>()) ?? {};
const { companyUrls = [], minHeadcountDeltaPct = 10 } = input;

if (companyUrls.length === 0) {
  console.warn('No companyUrls provided. Exiting.');
  await Actor.exit();
  process.exit(0);
}

console.log(
  `Starting linkedin-headcount-actor: ${companyUrls.length} companies, minDelta=${minHeadcountDeltaPct}%`
);

// Open KV store for snapshot persistence
const kvStore = await openKeyValueStoreWithFallback('linkedin-headcount-snapshots');

// Call the LinkedIn company scraper
let scrapedCompanies: LinkedinCompanyResult[] = [];
try {
  const run = await Actor.call('apify/linkedin-company-scraper', {
    startUrls: companyUrls.map((url) => ({ url })),
    proxy: { useApifyProxy: true },
  });

  if (run?.defaultDatasetId) {
    const dataset = await Actor.openDataset(run.defaultDatasetId);
    const { items } = await dataset.getData();
    scrapedCompanies = items as LinkedinCompanyResult[];
    console.log(`Retrieved ${scrapedCompanies.length} company profiles`);
  }
} catch (err) {
  console.error('Error calling apify/linkedin-company-scraper:', err);
}

// Process each company
const signals: Signal[] = [];

for (const company of scrapedCompanies) {
  const companyName = company.name ?? company.companyName ?? 'Unknown';
  const currentHeadcount = company.employeeCount ?? company.staffCount ?? company.numberOfEmployees ?? 0;
  const locations = (company.locations ?? []).map(
    (l) => [l.city, l.geographicArea, l.country].filter(Boolean).join(', ')
  );
  const domain = extractDomain(company.websiteUrl ?? company.website ?? '');

  if (currentHeadcount === 0) {
    console.warn(`No headcount data for ${companyName}, skipping`);
    continue;
  }

  const now = new Date().toISOString();
  const currentSnapshot: HeadcountSnapshot = {
    company: companyName,
    headcount: currentHeadcount,
    locations,
    timestamp: now,
  };

  const previousSnapshot = await loadSnapshot(kvStore, companyName);

  if (previousSnapshot) {
    const delta = currentHeadcount - previousSnapshot.headcount;
    const deltaPct = previousSnapshot.headcount > 0
      ? (delta / previousSnapshot.headcount) * 100
      : 0;

    console.log(
      `${companyName}: headcount ${previousSnapshot.headcount} → ${currentHeadcount} (${deltaPct.toFixed(1)}%)`
    );

    if (deltaPct >= minHeadcountDeltaPct && delta > 0) {
      const signalDate = now.split('T')[0];
      const id = createHash('sha256')
        .update(`${companyName}:headcount:${signalDate}`)
        .digest('hex')
        .slice(0, 16);

      const signal: Signal = {
        id,
        company: companyName,
        company_domain: domain,
        signal_type: 'headcount',
        signal_text: `${companyName} grew headcount by ${deltaPct.toFixed(0)}% (${previousSnapshot.headcount} → ${currentHeadcount} employees)`,
        source_url: company.url ?? company.linkedInUrl ?? '',
        date: now,
        raw: {
          previous_headcount: previousSnapshot.headcount,
          current_headcount: currentHeadcount,
          delta,
          delta_pct: parseFloat(deltaPct.toFixed(2)),
          previous_snapshot_date: previousSnapshot.timestamp,
          current_locations: locations,
        },
      };

      signals.push(signal);
    } else if (deltaPct < 0) {
      console.log(`${companyName} headcount declined — no signal emitted`);
    }
  } else {
    console.log(
      `No previous snapshot for ${companyName} — recording baseline headcount: ${currentHeadcount}`
    );
  }

  // Always save the latest snapshot
  await saveSnapshot(kvStore, companyName, currentSnapshot);
}

console.log(`Generated ${signals.length} headcount growth signals`);

// Push to dataset
await pushDataWithFallback(signals, DATASETS.SIGNALS);

console.log(`Pushed ${signals.length} headcount signals to dataset '${DATASETS.SIGNALS}'`);

await Actor.exit();

function extractDomain(websiteUrl: string): string {
  try {
    const parsed = new URL(websiteUrl.startsWith('http') ? websiteUrl : `https://${websiteUrl}`);
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return websiteUrl.toLowerCase().replace(/[^a-z0-9.-]/g, '').slice(0, 60);
  }
}
