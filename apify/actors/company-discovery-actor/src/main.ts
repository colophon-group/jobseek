/**
 * @actor company-discovery-actor
 *
 * Self-evolving company discovery system.
 *
 * Static sources (always run):
 *   1. Greenhouse Boards API  — 790+ confirmed board tokens
 *   2. The Muse API           — Company directory with job counts
 *   3. Arbeitnow API          — EU/remote job aggregator
 *   4. Remotive API           — Remote job aggregator
 *   5. Mega Employers         — Curated list of 150+ global giants
 *   6. Hiring.cafe            — Job board; job counts memorised in KV for delta tracking
 *   7. Himalayas              — Remote job aggregator with public paginated JSON API
 *   8. SmartRecruiters        — Enterprise ATS (Fortune 500) company probe
 *   9. Y Combinator           — YC-funded company directory (~5,800 companies)
 *
 * AI-powered discovery (runs when GOOGLE_AI_API_KEY is set):
 *   - Loads portal registry from KV store (persisted across runs)
 *   - Asks Gemini to analyse current coverage and suggest new portals
 *   - Probes candidates, promotes successful ones to "active"
 *   - Scrapes all active portals (static + AI-discovered)
 *   - Saves updated registry back to KV store
 */

import { Actor, log } from 'apify';
import type { CompanyDiscovery } from './types.js';
import { discoverFromGreenhouse } from './sources/greenhouse.js';
import { discoverFromTheMuse } from './sources/themuse.js';
import { discoverFromArbeitnow } from './sources/arbeitnow.js';
import { discoverFromRemotive } from './sources/remotive.js';
import { discoverFromMegaEmployers } from './sources/megaemployers.js';
import { discoverFromHiringCafe } from './sources/hiring-cafe.js';
import { discoverFromHimalayas } from './sources/himalayas.js';
import { discoverFromYCombinator } from './sources/ycombinator.js';
import { suggestNewPortals } from './sources/ai-discovery.js';
import { probePortal, validatePortal } from './sources/generic-portal.js';
import { loadRegistry, saveRegistry, getActivePortals, upsertPortal } from './registry.js';

interface Input {
  sources?: string[];
  maxCompaniesPerSource?: number;
  enableAiDiscovery?: boolean;
  maxAiSuggestionsPerRun?: number;
}

await Actor.init();

const input = (await Actor.getInput<Input>()) ?? {};
const {
  sources = ['greenhouse', 'themuse', 'megaemployers', 'arbeitnow', 'remotive', 'hiring-cafe', 'himalayas', 'smartrecruiters', 'ycombinator'],
  maxCompaniesPerSource = 1000,
  enableAiDiscovery = true,
  maxAiSuggestionsPerRun = 4,
} = input;

const googleAiKey = process.env.GOOGLE_AI_API_KEY;
const runAiDiscovery = enableAiDiscovery && !!googleAiKey;

log.info('Starting company-discovery-actor', { sources, maxCompaniesPerSource, aiDiscovery: runAiDiscovery });

// ── Load portal registry ──────────────────────────────────────────────────
const registry = await loadRegistry();

// ── AI Discovery: suggest + probe new portals ─────────────────────────────
if (runAiDiscovery) {
  try {
    const candidates = await suggestNewPortals(registry, googleAiKey!);
    log.info(`Gemini suggested ${candidates.length} new candidate portals`);

    for (const candidate of candidates.slice(0, maxAiSuggestionsPerRun)) {
      candidate.status = 'probing';
      candidate.lastProbedAt = new Date().toISOString();
      upsertPortal(registry, candidate);

      const valid = await validatePortal(candidate);
      candidate.status = valid ? 'active' : 'failed';
      if (valid) {
        candidate.lastSuccessAt = new Date().toISOString();
        log.info(`✓ New portal validated: ${candidate.id}`);
      } else {
        log.warning(`✗ Probe failed: ${candidate.id}`);
      }
      upsertPortal(registry, candidate);
    }
  } catch (err) {
    log.error(`AI discovery failed: ${err}`);
  }
}

// ── Run all sources ───────────────────────────────────────────────────────
const allCompanies: CompanyDiscovery[] = [];
const globalSeen = new Set<string>();
const sourceStats: Record<string, { companies: number; jobs: number; error?: string }> = {};

type SourceFn = () => Promise<CompanyDiscovery[]>;

const staticSourceMap: Record<string, SourceFn> = {
  greenhouse:    () => discoverFromGreenhouse(maxCompaniesPerSource),
  themuse:       () => discoverFromTheMuse(maxCompaniesPerSource),
  megaemployers: () => Promise.resolve(discoverFromMegaEmployers()),
  arbeitnow:     () => discoverFromArbeitnow(),
  remotive:      () => discoverFromRemotive(),
  'hiring-cafe': () => discoverFromHiringCafe(30),
  himalayas:     () => discoverFromHimalayas(),
  ycombinator:   () => discoverFromYCombinator(),
};

async function runSource(sourceId: string, fn: SourceFn): Promise<void> {
  log.info(`--- Running source: ${sourceId} ---`);
  const t0 = Date.now();
  try {
    const companies = await fn();
    let uniqueNew = 0, uniqueJobs = 0;
    for (const c of companies) {
      const key = c.company_name.toLowerCase().trim();
      if (!globalSeen.has(key)) {
        globalSeen.add(key);
        allCompanies.push(c);
        uniqueNew++;
        uniqueJobs += c.estimated_jobs;
      }
    }
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    sourceStats[sourceId] = { companies: uniqueNew, jobs: uniqueJobs };
    log.info(`${sourceId}: ${companies.length} total, ${uniqueNew} new unique (${elapsed}s)`);
  } catch (err) {
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    sourceStats[sourceId] = { companies: 0, jobs: 0, error: String(err) };
    log.error(`${sourceId} failed after ${elapsed}s: ${err}`);
  }
}

// 1. Static sources
for (const sourceId of sources) {
  const fn = staticSourceMap[sourceId];
  if (!fn) { log.warning(`Unknown source "${sourceId}", skipping`); continue; }
  await runSource(sourceId, fn);
}

// 2. All non-static active portals (AI-discovered + hardcoded generic portals like smartrecruiters)
const aiActivePortals = getActivePortals(registry).filter(
  p => !staticSourceMap[p.id],
);
for (const portal of aiActivePortals) {
  await runSource(portal.id, async () => {
    const { companies, error } = await probePortal(portal);
    if (error) throw new Error(error);
    portal.lastSuccessAt = new Date().toISOString();
    portal.companiesFound = companies.length;
    upsertPortal(registry, portal);
    return companies;
  });
}

// ── Update registry stats for static portals ─────────────────────────────
for (const [sourceId, stats] of Object.entries(sourceStats)) {
  const portal = registry.portals.find(p => p.id === sourceId);
  if (portal && !stats.error) {
    portal.lastSuccessAt = new Date().toISOString();
    portal.companiesFound = stats.companies;
    upsertPortal(registry, portal);
  }
}

await saveRegistry(registry);

// ── Push results ──────────────────────────────────────────────────────────
log.info(`Pushing ${allCompanies.length} unique companies to dataset...`);
await Actor.pushData(allCompanies);

// Registry summary record (readable by /agentic/api/discovery)
await Actor.pushData({
  _type: 'registry_summary',
  runAt: new Date().toISOString(),
  totalPortals: registry.portals.length,
  activePortals: registry.portals.filter(p => p.status === 'active').length,
  aiDiscoveredPortals: registry.portals.filter(p => p.suggestedBy === 'gemini' && p.status === 'active').length,
  portals: registry.portals.map(p => ({
    id: p.id, name: p.name, status: p.status, suggestedBy: p.suggestedBy,
    companiesFound: p.companiesFound ?? 0, lastSuccessAt: p.lastSuccessAt,
    geminiReasoning: p.geminiReasoning,
  })),
});

const totalJobs = allCompanies.reduce((s, c) => s + c.estimated_jobs, 0);
log.info('=== Summary ===');
log.info(`Companies: ${allCompanies.length}, Jobs: ${totalJobs.toLocaleString()}`);
log.info(`Registry: ${registry.portals.length} portals (${registry.portals.filter(p => p.status === 'active').length} active)`);

await Actor.exit();
