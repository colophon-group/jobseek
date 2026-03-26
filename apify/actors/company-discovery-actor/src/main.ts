/**
 * @actor company-discovery-actor
 *
 * Discovers companies with open positions from job aggregators and public ATS APIs:
 *   1. Greenhouse Boards API — 790+ confirmed board tokens, exact job counts
 *   2. The Muse API         — Company directory with job counts per company
 *   3. Arbeitnow API        — EU/remote job listings aggregated by company
 *   4. Remotive API          — Remote job listings aggregated by company
 *   5. Mega Employers        — Curated list of 150+ global giants (India, China, US, EU)
 *
 * Output: CompanyDiscovery[] → { company_name, job_board_url, estimated_jobs, source }
 */

import { Actor, log } from 'apify';
import type { CompanyDiscovery } from './types.js';
import { discoverFromGreenhouse } from './sources/greenhouse.js';
import { discoverFromTheMuse } from './sources/themuse.js';
import { discoverFromArbeitnow } from './sources/arbeitnow.js';
import { discoverFromRemotive } from './sources/remotive.js';
import { discoverFromMegaEmployers } from './sources/megaemployers.js';

interface Input {
  sources?: string[];
  maxCompaniesPerSource?: number;
}

await Actor.init();

const input = (await Actor.getInput<Input>()) ?? {};
const {
  sources = ['greenhouse', 'themuse', 'megaemployers', 'arbeitnow', 'remotive'],
  maxCompaniesPerSource = 1000,
} = input;

log.info('Starting company-discovery-actor', { sources, maxCompaniesPerSource });

const allCompanies: CompanyDiscovery[] = [];
const globalSeen = new Set<string>();

type SourceFn = () => Promise<CompanyDiscovery[]>;

const sourceMap: Record<string, SourceFn> = {
  greenhouse: () => discoverFromGreenhouse(maxCompaniesPerSource),
  themuse: () => discoverFromTheMuse(maxCompaniesPerSource),
  megaemployers: () => Promise.resolve(discoverFromMegaEmployers()),
  arbeitnow: () => discoverFromArbeitnow(),
  remotive: () => discoverFromRemotive(),
};

const sourceStats: Record<string, { companies: number; jobs: number; error?: string }> = {};

for (const source of sources) {
  const runner = sourceMap[source];
  if (!runner) {
    log.warning(`Unknown source "${source}", skipping`);
    continue;
  }

  log.info(`--- Running source: ${source} ---`);
  const startTime = Date.now();

  try {
    const companies = await runner();

    let uniqueNew = 0;
    let uniqueJobs = 0;
    for (const company of companies) {
      const key = company.company_name.toLowerCase().trim();
      if (!globalSeen.has(key)) {
        globalSeen.add(key);
        allCompanies.push(company);
        uniqueNew++;
        uniqueJobs += company.estimated_jobs;
      }
    }

    const totalJobs = companies.reduce((s, c) => s + c.estimated_jobs, 0);
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    sourceStats[source] = { companies: uniqueNew, jobs: uniqueJobs };
    log.info(`${source}: ${companies.length} total, ${uniqueNew} unique new, ${totalJobs.toLocaleString()} jobs (${elapsed}s)`);
  } catch (err) {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    sourceStats[source] = { companies: 0, jobs: 0, error: String(err) };
    log.error(`${source} failed after ${elapsed}s: ${err}`);
  }
}

// Push all results to default dataset
log.info(`Pushing ${allCompanies.length} unique companies to dataset...`);
await Actor.pushData(allCompanies);

// Summary
const totalJobs = allCompanies.reduce((s, c) => s + c.estimated_jobs, 0);
log.info('=== Discovery Summary ===');
log.info(`Total unique companies: ${allCompanies.length}`);
log.info(`Total estimated jobs: ${totalJobs.toLocaleString()}`);
for (const [source, stats] of Object.entries(sourceStats)) {
  if (stats.error) {
    log.info(`  ${source}: FAILED — ${stats.error}`);
  } else {
    log.info(`  ${source}: ${stats.companies} companies, ${stats.jobs.toLocaleString()} jobs`);
  }
}

await Actor.exit();
