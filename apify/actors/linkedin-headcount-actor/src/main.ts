/**
 * @actor linkedin-headcount-actor
 *
 * Tracks LinkedIn company headcount over time via snapshot comparison.
 * Emits a signal when growth exceeds minHeadcountDeltaPct (default 10%).
 * Signal type: `headcount`
 */

import { Actor } from 'apify';
import { runSignalActor } from '../../../shared/signalActor';
import { signalId } from '../../../shared/id';
import { extractDomain } from '../../../shared/utils';
import type { Signal } from '../../../shared/types';
import { openKeyValueStoreWithFallback } from '../../../shared/storage';
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

runSignalActor<LinkedinHeadcountInput>(async (input) => {
  const { companyUrls = [], minHeadcountDeltaPct = 10 } = input;

  if (companyUrls.length === 0) {
    console.warn('No companyUrls provided.');
    return [];
  }

  console.log(`linkedin-headcount-actor: ${companyUrls.length} companies, minDelta=${minHeadcountDeltaPct}%`);

  const kvStore = await openKeyValueStoreWithFallback('linkedin-headcount-snapshots');

  // Scrape company profiles
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
      company: companyName, headcount: currentHeadcount, locations, timestamp: now,
    };

    const previousSnapshot = await loadSnapshot(kvStore, companyName);

    if (previousSnapshot) {
      const delta = currentHeadcount - previousSnapshot.headcount;
      const deltaPct = previousSnapshot.headcount > 0
        ? (delta / previousSnapshot.headcount) * 100
        : 0;

      console.log(`${companyName}: ${previousSnapshot.headcount} → ${currentHeadcount} (${deltaPct.toFixed(1)}%)`);

      if (deltaPct >= minHeadcountDeltaPct && delta > 0) {
        signals.push({
          id: signalId(companyName, 'headcount', now.split('T')[0]),
          company: companyName,
          company_domain: domain,
          signal_type: 'headcount',
          signal_text: `${companyName} grew headcount by ${deltaPct.toFixed(0)}% (${previousSnapshot.headcount} → ${currentHeadcount})`,
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
        });
      }
    } else {
      console.log(`No previous snapshot for ${companyName} — recording baseline: ${currentHeadcount}`);
    }

    await saveSnapshot(kvStore, companyName, currentSnapshot);
  }

  return signals;
});
