/**
 * @actor company-discovery-actor
 *
 * Self-evolving company discovery system.
 *
 * Static sources (always run):
 *   1.  Greenhouse API          — 790+ confirmed board tokens (boards-api.greenhouse.io)
 *   2.  Greenhouse CDX          — Wayback CDX wildcard for boards.greenhouse.io/* (new tokens)
 *   3.  The Muse API            — Company directory with job counts
 *   4.  Arbeitnow API           — EU/remote job aggregator
 *   5.  Remotive API            — Remote job aggregator
 *   6.  Mega Employers          — Curated list of 150+ global giants
 *   7.  Hiring.cafe             — Job board; job counts memorised in KV for delta tracking
 *   8.  Himalayas               — Remote job aggregator with public paginated JSON API
 *   9.  Y Combinator            — YC-funded company directory (~5,800 companies)
 *  10.  Ashby CDX               — jobs.ashbyhq.com/* (dominant YC/VC startup ATS)
 *  11.  Lever CDX               — jobs.lever.co/* (popular tech ATS)
 *  12.  Workable CDX            — apply.workable.com/* (SMB/scale-up ATS)
 *  13.  BambooHR CDX            — *.bamboohr.com (mid-market ATS)
 *  14.  Recruitee CDX           — *.recruitee.com (EU mid-market ATS)
 *  15.  JazzHR CDX              — *.applytojob.com (SMB US ATS)
 *  16.  BreezyHR CDX            — *.breezy.hr (SMB ATS)
 *  17.  iCIMS CDX               — *.icims.com/jobs/* (enterprise ATS, 4000+ companies)
 *  18.  Taleo CDX               — *.taleo.net/careersection* (Oracle enterprise, 5000+ companies)
 *  19.  SmartRecruiters          — Fortune 500 ATS; run via registry/probe path
 *  20.  LinkedIn guest API       — Public job search, extracts hiring companies
 *  21.  Indeed companies         — Company directory with job counts
 *  22.  Glassdoor browse         — Employer directory with ratings/job counts
 *  23.  Workday CDX              — *.myworkdayjobs.com (Fortune 500 / enterprise ATS, 5000+ companies)
 *  24.  SmartRecruiters CDX     — jobs.smartrecruiters.com/* (Fortune 500 ATS via CDX path scan)
 *  25.  Wellfound CDX           — wellfound.com/company/* (AngelList Talent, 10,000+ startups)
 *  26.  We Work Remotely        — RSS feeds for 11 remote job categories
 *  27.  Softgarden CDX          — *.softgarden.io (dominant DACH/German ATS)
 *  28.  JOIN CDX                — join.com/companies/* (EU startup job board)
 *  29.  Pinpoint HQ CDX         — app.pinpointhq.com/{company} (UK/EU Series A–C ATS)
 *  30.  Comeet CDX              — recruiting.comeet.co/jobs/{company} (Israel/EU/US tech ATS)
 *  31.  Fountain CDX            — jobs.fountain.com/{company} (gig/shift-work ATS: Uber, DoorDash, Instacart)
 *  32.  Rippling CDX            — ats.rippling.com/{company} (modern HCM/ATS: Series B+ tech scale-ups)
 *  33.  Ashby Boards CDX        — boards.ashbyhq.com/{company} (alternative Ashby domain, extends coverage)
 *  34.  Factorial HR CDX        — factorialhr.com/job_postings/{company} (EU/LATAM HCM/ATS, Spain-origin)
 *  35.  Kenjo CDX               — app.kenjo.io/{company}/jobs (European ATS, DACH/Spain/UK)
 *  36.  Workstream CDX          — jobs.workstream.us/{company} (hourly/shift-work ATS: Chick-fil-A, McDonald's, Walmart)
 *  37.  Dover CDX               — talent.dover.com/jobs/{company} (VC-backed startup ATS)
 *  38.  Jobteaser CDX           — jobteaser.com/{locale}/company/{slug}/jobs (EU campus/student job board)
 *  39.  WTTJ CDX               — welcometothejungle.com/{locale}/companies/{slug}/jobs (leading French/EU job board)
 *  40.  Freshteam CDX          — {company}.freshteam.com/jobs (Freshworks ATS, global/Asia-APAC)
 *  41.  Homerun CDX            — {company}.homerun.co (Dutch/EU startup ATS, Netherlands-origin)
 *  42.  HiBob CDX             — app.hibob.com/careers/{company} (modern HRIS/ATS: JetBrains, monday.com, Wix)
 *  43.  Eightfold CDX         — careers.eightfold.ai/{company} (AI talent platform: Prudential, Chevron, NTT Data)
 *  44.  Cornerstone CDX      — {tenant}.csod.com/careers (Fortune 500 enterprise ATS: Boeing, Adobe, FedEx, UnitedHealth)
 *  45.  PageUp CDX           — jobs.pageuppeople.com/{company}/go (APAC enterprise ATS: Qantas, ANZ, BHP, universities)
 *  46.  Avature CDX          — careers.avature.net/{company} (Fortune 500 talent platform: Amazon, EY, PwC, NASA)
 *  47.  Hireology CDX        — {company}.hireology.com/jobs (automotive/franchise/retail ATS)
 *  48.  Zoho Recruit CDX     — {company}.zohorecruit.com/jobs (global SMB ATS)
 *  49.  TalentLyft CDX       — {company}.talentlyft.com/jobs (EU ATS: Croatia-origin, DACH/CEE)
 *  50.  Occupop CDX          — {company}.occupop.com/jobs (Irish/UK enterprise ATS)
 *  51.  Paycor CDX           — {company}.paycor.com/career-portal (US HCM/ATS: healthcare, retail, manufacturing)
 *  52.  ClearCompany CDX     — {company}.clearcompany.com/careers (US mid-market ATS: healthcare, education)
 *  53.  Darwinbox CDX        — {company}.darwinbox.com/ms/candidate/jobs (India enterprise HCM: Swiggy, Zomato, Puma, JSW)
 *  54.  Keka CDX             — {company}.keka.com/careers (India HR/ATS platform: mid-market APAC)
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
import { discoverFromBambooHR } from './sources/bamboohr.js';
import { discoverFromRecruitee } from './sources/recruitee.js';
import { discoverFromWorkable } from './sources/workable.js';
import { discoverFromAshby } from './sources/ashby.js';
import { discoverFromLever } from './sources/lever.js';
import { discoverFromGreenhouseCdx } from './sources/greenhouse-cdx.js';
import { discoverFromJazzHR, discoverFromTaleo } from './sources/jazzhr.js';
import { discoverFromBreezyHR, discoverFromICIMS, discoverFromFreshteam, discoverFromHomerun, discoverFromHiBob, discoverFromHireology, discoverFromZohoRecruit, discoverFromDarwinbox, discoverFromKeka } from './sources/breezyhr.js';
import { discoverFromTeamtailor } from './sources/teamtailor.js';
import { discoverFromPersonio, discoverFromJobvite, discoverFromSuccessFactors, discoverFromSmartRecruiters, discoverFromPinpoint, discoverFromComeet, discoverFromCornerstone } from './sources/personio.js';
import { discoverFromLinkedIn } from './sources/linkedin.js';
import { discoverFromIndeed } from './sources/indeed.js';
import { discoverFromGlassdoor } from './sources/glassdoor.js';
import { discoverFromStepstone } from './sources/stepstone.js';
import { discoverFromXing } from './sources/xing.js';
import { discoverFromWorkdayCdx } from './sources/workday-cdx.js';
import { discoverFromWellfound } from './sources/wellfound.js';
import { discoverFromRemoteOK } from './sources/remoteok.js';
import { discoverFromWeWorkRemotely } from './sources/weworkremotely.js';
import { discoverFromSoftgarden, discoverFromJoin, discoverFromWelcomeToTheJungle, discoverFromTalentLyft, discoverFromOccupop, discoverFromEasyCruit, discoverFromVarbi } from './sources/softgarden.js';
import { discoverFromFountain } from './sources/fountain.js';
import { discoverFromRippling, discoverFromAshbyBoards } from './sources/rippling.js';
import { discoverFromFactorial, discoverFromKenjo, discoverFromWorkstream, discoverFromDover, discoverFromJobteaser, discoverFromEightfold, discoverFromPageUp, discoverFromAvature, discoverFromPaycor, discoverFromClearCompany, discoverFromDayforce } from './sources/factorial.js';
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
  sources = ['greenhouse', 'themuse', 'megaemployers', 'arbeitnow', 'remotive', 'remoteok', 'hiring-cafe', 'himalayas', 'ycombinator', 'bamboohr', 'recruitee', 'workable', 'ashby', 'lever', 'greenhouse-cdx', 'jazzhr', 'breezyhr', 'homerun', 'hibob', 'eightfold', 'cornerstone', 'pageup', 'avature', 'hireology', 'zohorecruit', 'talentlyft', 'occupop', 'easycruit', 'varbi', 'paycor', 'clearcompany', 'dayforce', 'darwinbox', 'keka', 'teamtailor', 'personio', 'icims', 'taleo', 'jobvite', 'successfactors', 'smartrecruiters', 'pinpoint', 'comeet', 'fountain', 'rippling', 'ashby-boards', 'factorial', 'kenjo', 'workstream', 'dover', 'jobteaser', 'wttj', 'freshteam', 'linkedin', 'indeed', 'glassdoor', 'stepstone', 'xing', 'workday-cdx', 'wellfound', 'weworkremotely', 'softgarden', 'join'],
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
  remoteok:      () => discoverFromRemoteOK(),
  'hiring-cafe': () => discoverFromHiringCafe(50),
  himalayas:     () => discoverFromHimalayas(),
  ycombinator:   () => discoverFromYCombinator(),
  bamboohr:      () => discoverFromBambooHR(),
  recruitee:     () => discoverFromRecruitee(),
  workable:      () => discoverFromWorkable(),
  ashby:         () => discoverFromAshby(),
  lever:         () => discoverFromLever(),
  'greenhouse-cdx': () => discoverFromGreenhouseCdx(),
  jazzhr:        () => discoverFromJazzHR(),
  breezyhr:      () => discoverFromBreezyHR(),
  homerun:       () => discoverFromHomerun(),
  teamtailor:    () => discoverFromTeamtailor(),
  personio:      () => discoverFromPersonio(),
  icims:         () => discoverFromICIMS(),
  freshteam:     () => discoverFromFreshteam(),
  taleo:         () => discoverFromTaleo(),
  jobvite:       () => discoverFromJobvite(),
  successfactors:  () => discoverFromSuccessFactors(),
  smartrecruiters: () => discoverFromSmartRecruiters(),
  pinpoint:        () => discoverFromPinpoint(),
  comeet:          () => discoverFromComeet(),
  linkedin:      () => discoverFromLinkedIn(undefined, maxCompaniesPerSource),
  indeed:        () => discoverFromIndeed(undefined, maxCompaniesPerSource),
  glassdoor:     () => discoverFromGlassdoor(undefined, maxCompaniesPerSource),
  stepstone:     () => discoverFromStepstone(undefined, maxCompaniesPerSource),
  xing:          () => discoverFromXing(undefined, maxCompaniesPerSource),
  'workday-cdx': () => discoverFromWorkdayCdx(),
  wellfound:       () => discoverFromWellfound(),
  weworkremotely:  () => discoverFromWeWorkRemotely(),
  softgarden:      () => discoverFromSoftgarden(),
  join:            () => discoverFromJoin(),
  wttj:            () => discoverFromWelcomeToTheJungle(),
  fountain:        () => discoverFromFountain(),
  rippling:        () => discoverFromRippling(),
  'ashby-boards':  () => discoverFromAshbyBoards(),
  factorial:       () => discoverFromFactorial(),
  kenjo:           () => discoverFromKenjo(),
  workstream:      () => discoverFromWorkstream(),
  dover:           () => discoverFromDover(),
  jobteaser:       () => discoverFromJobteaser(),
  hibob:           () => discoverFromHiBob(),
  eightfold:       () => discoverFromEightfold(),
  cornerstone:     () => discoverFromCornerstone(),
  pageup:          () => discoverFromPageUp(),
  avature:         () => discoverFromAvature(),
  hireology:       () => discoverFromHireology(),
  zohorecruit:     () => discoverFromZohoRecruit(),
  darwinbox:       () => discoverFromDarwinbox(),
  keka:            () => discoverFromKeka(),
  talentlyft:      () => discoverFromTalentLyft(),
  occupop:         () => discoverFromOccupop(),
  paycor:          () => discoverFromPaycor(),
  clearcompany:    () => discoverFromClearCompany(),
  dayforce:        () => discoverFromDayforce(),
  easycruit:       () => discoverFromEasyCruit(),
  varbi:           () => discoverFromVarbi(),
};

/** Fetch a single source; returns raw results without mutating shared state. */
async function fetchSource(sourceId: string, fn: SourceFn): Promise<{ sourceId: string; companies: CompanyDiscovery[]; elapsed: string; error?: string }> {
  const t0 = Date.now();
  try {
    const companies = await fn();
    return { sourceId, companies, elapsed: ((Date.now() - t0) / 1000).toFixed(1) };
  } catch (err) {
    return { sourceId, companies: [], elapsed: ((Date.now() - t0) / 1000).toFixed(1), error: String(err) };
  }
}

/** Merge a batch of source results into shared state. */
function mergeResults(batch: Awaited<ReturnType<typeof fetchSource>>[]): void {
  for (const { sourceId, companies, elapsed, error } of batch) {
    if (error) {
      sourceStats[sourceId] = { companies: 0, jobs: 0, error };
      log.error(`${sourceId} failed after ${elapsed}s: ${error}`);
      continue;
    }
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
    sourceStats[sourceId] = { companies: uniqueNew, jobs: uniqueJobs };
    log.info(`${sourceId}: ${companies.length} total, ${uniqueNew} new unique (${elapsed}s)`);
  }
}

async function runSource(sourceId: string, fn: SourceFn): Promise<void> {
  const result = await fetchSource(sourceId, fn);
  mergeResults([result]);
}

// 1. Static sources — run in parallel batches of 6
// CDX sources share Wayback bandwidth; API sources run independently. Batch size balances speed vs rate limits.
const BATCH_SIZE = 6;
const sourceEntries = sources
  .map(id => ({ id, fn: staticSourceMap[id] }))
  .filter(({ id, fn }) => { if (!fn) { log.warning(`Unknown source "${id}", skipping`); return false; } return true; });

for (let i = 0; i < sourceEntries.length; i += BATCH_SIZE) {
  const batch = sourceEntries.slice(i, i + BATCH_SIZE);
  log.info(`--- Running source batch ${Math.floor(i / BATCH_SIZE) + 1}: ${batch.map(b => b.id).join(', ')} ---`);
  const results = await Promise.all(batch.map(({ id, fn }) => fetchSource(id, fn)));
  mergeResults(results);
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
