import { Actor, log } from 'apify';
import { fetchCdxSnapshots } from './cdx.js';
import { fetchArchivedPage } from './fetch.js';
import { extractJobs } from './extractors/index.js';
import { buildJobRegistry, computeGhostStats, registryToTimeline } from './ghost.js';
import {
  fetchUrlInventory,
  filterJobUrls,
  buildJobRecords,
} from './inventory.js';
import { analyzeWithGemini, discoverGhostCompanies } from './gemini.js';
import type {
  Input,
  CompanyInput,
  DayResult,
  SummaryRecord,
  GhostAnalysis,
  JobRecord,
  TimelinePoint,
  BatchSummaryRecord,
} from './types.js';

// ── Built-in seed: companies with confirmed Wayback coverage + ghost-job signals ─
// Verified via CDX API — snapshot mode (inventoryMode: false) for Greenhouse/Lever.
// Workday portals rarely appear in Wayback; use snapshot mode for listing pages.
const SEED_COMPANIES: CompanyInput[] = [
  // Greenhouse boards — tech layoffs while posting
  { name: 'Coinbase',      portalUrl: 'https://boards.greenhouse.io/coinbase',      inventoryMode: false },  // 18% layoff Jun 2022
  { name: 'Lyft',          portalUrl: 'https://boards.greenhouse.io/lyft',          inventoryMode: false },  // 26% layoff Nov 2022
  { name: 'Figma',         portalUrl: 'https://boards.greenhouse.io/figma',         inventoryMode: false },  // Adobe deal collapse
  { name: 'Notion',        portalUrl: 'https://boards.greenhouse.io/notion',        inventoryMode: false },  // post-hypergrowth slowdown
  { name: 'OpenAI',        portalUrl: 'https://boards.greenhouse.io/openai',        inventoryMode: false },  // recurring hiring freezes
  { name: 'Twilio',        portalUrl: 'https://boards.greenhouse.io/twilio',        inventoryMode: false },  // 17% layoff Jan 2023
  { name: 'Anthropic',     portalUrl: 'https://boards.greenhouse.io/anthropic',     inventoryMode: false },  // rapid headcount swings
  // Lever boards
  { name: 'Scale AI',      portalUrl: 'https://jobs.lever.co/scaleai',              inventoryMode: false },
  { name: 'Brex',          portalUrl: 'https://jobs.lever.co/brex',                 inventoryMode: false },
  { name: 'Rippling',      portalUrl: 'https://jobs.lever.co/rippling',             inventoryMode: false },
  // Big 4 consulting — evergreen pipelines, slow hiring
  { name: 'Deloitte',      portalUrl: 'https://boards.greenhouse.io/deloitte',      inventoryMode: false },
  { name: 'PwC',           portalUrl: 'https://boards.greenhouse.io/pwc',           inventoryMode: false },
  // Defense contractors — clearance delays mask ghost posts
  { name: 'Booz Allen',    portalUrl: 'https://jobs.lever.co/boozallen',            inventoryMode: false },
  // Fintech / banks
  { name: 'Robinhood',     portalUrl: 'https://boards.greenhouse.io/robinhood',     inventoryMode: false },  // 23% layoff + ongoing posts
  { name: 'Stripe',        portalUrl: 'https://boards.greenhouse.io/stripe',        inventoryMode: false },  // long-open senior roles
  // SmartRecruiters (Fortune 500 firms notorious for ghost posts)
  { name: 'Bosch',         portalUrl: 'https://jobs.smartrecruiters.com/BoschGroup',    inventoryMode: false },
  { name: 'Sephora',       portalUrl: 'https://jobs.smartrecruiters.com/SephoraUSA',    inventoryMode: false },
  { name: 'McDonald\'s',   portalUrl: 'https://jobs.smartrecruiters.com/McDonalds',     inventoryMode: false },
  { name: 'Lidl',          portalUrl: 'https://jobs.smartrecruiters.com/Lidl',          inventoryMode: false },
  // BambooHR boards — high-ghost SMB/mid-market companies
  { name: 'WP Engine',     portalUrl: 'https://wpengine.bamboohr.com/jobs',             inventoryMode: false },
  { name: 'Qualtrics',     portalUrl: 'https://qualtrics.bamboohr.com/jobs',            inventoryMode: false },
  { name: 'Canopy',        portalUrl: 'https://canopy.bamboohr.com/jobs',               inventoryMode: false },
];

await Actor.init();

const input = (await Actor.getInput<Input>()) ?? ({} as Input);
const {
  batchMode = false,
  companies,
  discoveryRounds = 2,
  portalUrl,
  startDate,
  endDate,
  maxSnapshots = 100,
  delayMs = 1500,
  inventoryMode = false,
  companyName,
} = input;

const googleAiApiKey =
  input.googleAiApiKey ??
  process.env.GOOGLE_AI_API_KEY ??
  '';

const today      = new Date().toISOString().slice(0, 10);
const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
const effectiveStart = startDate ?? oneYearAgo;
const effectiveEnd   = endDate   ?? today;

// ─────────────────────────────────────────────────────────────────────────────
// BATCH MODE — multi-company ghost discovery loop
// ─────────────────────────────────────────────────────────────────────────────
if (batchMode) {
  await runBatch();
  await Actor.exit();
}

// ─────────────────────────────────────────────────────────────────────────────
// SINGLE COMPANY MODE
// ─────────────────────────────────────────────────────────────────────────────
if (!portalUrl) throw new Error('Input field "portalUrl" is required (or set batchMode: true to use the seed list).');

const company = companyName ?? new URL(portalUrl).hostname;

log.info('Wayback Job History', {
  portalUrl,
  company,
  mode: inventoryMode ? 'inventory' : 'snapshot',
  start: effectiveStart,
  end: effectiveEnd,
  gemini: !!googleAiApiKey,
});

await analyzeCompany(company, portalUrl, inventoryMode);
await Actor.exit();

// =============================================================================

/**
 * Batch mode: iterate through company list, run analysis on each,
 * then ask Gemini to suggest more companies for subsequent rounds.
 */
async function runBatch() {
  const companyList: CompanyInput[] = companies?.length ? companies : SEED_COMPANIES;

  log.info(`Batch mode: ${companyList.length} seed companies, up to ${discoveryRounds} discovery rounds`, {
    gemini: !!googleAiApiKey,
    start: effectiveStart,
    end: effectiveEnd,
  });

  const processedUrls = new Set<string>();
  let queue: CompanyInput[] = [...companyList];
  const allResults: GhostAnalysis[] = [];

  for (let round = 1; round <= discoveryRounds; round++) {
    const toProcess = queue.filter(c => !processedUrls.has(c.portalUrl));
    if (toProcess.length === 0) {
      log.info(`Round ${round}: no new companies to analyze, stopping early`);
      break;
    }

    log.info(`=== Round ${round} — ${toProcess.length} companies ===`);
    const roundResults: GhostAnalysis[] = [];

    for (const co of toProcess) {
      processedUrls.add(co.portalUrl);
      log.info(`Analyzing: ${co.name}`, { portalUrl: co.portalUrl, inventoryMode: co.inventoryMode });
      try {
        const analysis = await analyzeCompany(
          co.name,
          co.portalUrl,
          co.inventoryMode ?? false,
        );
        if (analysis) {
          roundResults.push(analysis);
          allResults.push(analysis);
        }
      } catch (err) {
        log.warning(`Failed to analyze ${co.name}: ${err}`);
      }
    }

    // Discover new companies for the next round via Gemini
    let discoveredCompanies: CompanyInput[] = [];
    if (round < discoveryRounds && googleAiApiKey && allResults.length > 0) {
      try {
        log.info('Asking Gemini to suggest more ghost-job companies…');
        const suggested = await discoverGhostCompanies(googleAiApiKey, allResults, round);
        discoveredCompanies = suggested.filter(c => !processedUrls.has(c.portalUrl));
        queue = discoveredCompanies;
        log.info(`Gemini suggested ${discoveredCompanies.length} new companies for round ${round + 1}`);
      } catch (err) {
        log.warning(`Company discovery failed: ${err}`);
      }
    }

    // Push round summary
    const batchSummary: BatchSummaryRecord = {
      _type: 'batch-summary',
      round,
      companiesAnalyzed: roundResults.length,
      avgGhostRisk: roundResults.length > 0
        ? Math.round(roundResults.reduce((s, r) => s + r.overallGhostRisk, 0) / roundResults.length)
        : 0,
      worstOffenders: [...roundResults]
        .sort((a, b) => b.overallGhostRisk - a.overallGhostRisk)
        .slice(0, 5)
        .map(r => ({
          company: r.company,
          ghostRisk: r.overallGhostRisk,
          recommendation: r.recommendation,
        })),
      discoveredCompanies,
    };

    await Actor.pushData(batchSummary);
    log.info(`Round ${round} complete`, {
      analyzed: roundResults.length,
      avgGhostRisk: batchSummary.avgGhostRisk,
      worstOffender: batchSummary.worstOffenders[0]?.company ?? 'n/a',
    });
  }

  log.info('Batch complete', {
    totalCompanies: processedUrls.size,
    totalAnalyzed: allResults.length,
  });
}

/**
 * Run inventory or snapshot analysis for a single company.
 * Returns the GhostAnalysis record (also pushed to dataset).
 */
async function analyzeCompany(
  cmp: string,
  portal: string,
  useInventoryMode: boolean,
): Promise<GhostAnalysis | null> {
  if (useInventoryMode) {
    return runInventoryMode(cmp, portal);
  }
  return runSnapshotMode(cmp, portal);
}

// ─────────────────────────────────────────────────────────────────────────────
// MODE A: CDX Inventory — finds all archived job URLs, tracks duration
// Best for Workday / SPA portals where listing pages aren't well-archived.
// ─────────────────────────────────────────────────────────────────────────────
async function runInventoryMode(cmp: string, portal: string): Promise<GhostAnalysis | null> {
  log.info(`[${cmp}] INVENTORY mode — scanning all archived job URLs`);

  const allUrls = await fetchUrlInventory(
    portal,
    effectiveStart,
    effectiveEnd,
    maxSnapshots * 50,  // cast wider net since most won't be job pages
  );

  const jobUrls = filterJobUrls(allUrls);
  log.info(`[${cmp}] Filtered to ${jobUrls.length} job-page URLs from ${allUrls.length} total`);

  if (jobUrls.length === 0) {
    log.warning(`[${cmp}] No job URLs found in inventory. Try snapshot mode or broaden the date range.`);
    return null;
  }

  const limit = Math.min(jobUrls.length, maxSnapshots * 2);
  const jobRecords = await buildJobRecords(
    jobUrls.slice(0, limit),
    effectiveStart,
    effectiveEnd,
    delayMs,
  );

  for (const record of jobRecords) {
    await Actor.pushData({ ...record, _company: cmp });
  }

  const registry = new Map<string, JobRecord>(jobRecords.map(r => [r.url, r]));
  const stats    = computeGhostStats(registry);
  const timeline = registryToTimeline(registry);

  const analysis = await pushGhostAnalysis(cmp, portal, effectiveStart, effectiveEnd, stats, timeline);

  log.info(`[${cmp}] Inventory complete`, {
    totalJobUrls: jobRecords.length,
    ghostCandidates: stats.ghostCandidates,
    ghostRate: `${Math.round(stats.ghostRate * 100)}%`,
    ghostRisk: analysis.overallGhostRisk,
  });

  return analysis;
}

// ─────────────────────────────────────────────────────────────────────────────
// MODE B: Snapshot — fetch each daily Wayback snapshot and extract job listings
// Best for Greenhouse / Lever / Ashby / Workable portals.
// ─────────────────────────────────────────────────────────────────────────────
async function runSnapshotMode(cmp: string, portal: string): Promise<GhostAnalysis | null> {
  log.info(`[${cmp}] SNAPSHOT mode — processing daily Wayback snapshots`);

  const snapshots = await fetchCdxSnapshots({
    url: portal,
    startDate: effectiveStart,
    endDate: effectiveEnd,
    maxSnapshots,
  });

  if (snapshots.length === 0) {
    log.warning(`[${cmp}] No Wayback snapshots found. Try inventoryMode: true for SPA/Workday portals.`);
    return null;
  }

  log.info(`[${cmp}] Processing ${snapshots.length} daily snapshots`);

  const dayResults: DayResult[]   = [];
  const timeline: TimelinePoint[] = [];

  for (let i = 0; i < snapshots.length; i++) {
    const snap = snapshots[i];
    const date = `${snap.timestamp.slice(0, 4)}-${snap.timestamp.slice(4, 6)}-${snap.timestamp.slice(6, 8)}`;

    log.info(`[${cmp}] [${i + 1}/${snapshots.length}] ${date}`, { ts: snap.timestamp });

    const html = await fetchArchivedPage(snap.timestamp, snap.original);

    if (!html) {
      const rec: DayResult = {
        date, timestamp: snap.timestamp,
        snapshotUrl: `https://web.archive.org/web/${snap.timestamp}/${snap.original}`,
        jobCount: 0, jobs: [], extractionMethod: 'fetch-failed', error: 'fetch_failed',
      };
      await Actor.pushData({ ...rec, _company: cmp });
      dayResults.push(rec);
      timeline.push({ date, jobCount: 0 });
      await sleep(delayMs);
      continue;
    }

    const { jobs, method } = await extractJobs(html, snap);

    const rec: DayResult = {
      date, timestamp: snap.timestamp,
      snapshotUrl: `https://web.archive.org/web/${snap.timestamp}/${snap.original}`,
      jobCount: jobs.length, jobs, extractionMethod: method,
    };

    await Actor.pushData({ ...rec, _company: cmp });
    dayResults.push(rec);
    timeline.push({ date, jobCount: jobs.length });

    log.info(`[${cmp}]   → ${jobs.length} jobs (${method})`);
    await sleep(delayMs);
  }

  const jobRegistry = buildJobRegistry(dayResults);
  const stats       = computeGhostStats(jobRegistry);

  for (const record of jobRegistry.values()) {
    if (record.ghostScore >= 40) {
      await Actor.pushData({ _type: 'job-record', _company: cmp, ...record });
    }
  }

  const withJobs = timeline.filter(t => t.jobCount > 0);
  const avgJobCount = withJobs.length > 0
    ? Math.round(withJobs.reduce((s, t) => s + t.jobCount, 0) / withJobs.length)
    : 0;
  const peak = timeline.reduce(
    (best, t) => (t.jobCount > best.jobCount ? t : best),
    { date: '', jobCount: 0 },
  );

  const summary: SummaryRecord = {
    _type: 'summary',
    portalUrl: portal,
    startDate: effectiveStart,
    endDate: effectiveEnd,
    totalSnapshotsProcessed: snapshots.length,
    snapshotsWithJobs: withJobs.length,
    avgJobCount,
    peakDate: peak.date,
    peakJobCount: peak.jobCount,
    latestJobCount: timeline.at(-1)?.jobCount ?? 0,
    timeline,
    _company: cmp,
  };
  await Actor.pushData(summary);

  const analysis = await pushGhostAnalysis(
    cmp, portal, effectiveStart, effectiveEnd,
    stats, registryToTimeline(jobRegistry),
  );

  log.info(`[${cmp}] Snapshot complete`, {
    snapshots: snapshots.length,
    uniqueJobs: stats.totalUniqueJobs,
    ghostCandidates: stats.ghostCandidates,
    ghostRisk: analysis.overallGhostRisk,
  });

  return analysis;
}

// ─────────────────────────────────────────────────────────────────────────────

async function pushGhostAnalysis(
  cmp: string,
  portal: string,
  start: string,
  end: string,
  stats: ReturnType<typeof computeGhostStats>,
  timeline: TimelinePoint[],
): Promise<GhostAnalysis> {
  const base: GhostAnalysis = {
    _type: 'ghost-analysis',
    company: cmp,
    portalUrl: portal,
    analysisDate: new Date().toISOString().slice(0, 10),
    periodStart: start,
    periodEnd: end,
    ...stats,
    overallGhostRisk: 0,
    topGhostRoles: [],
    patterns: [],
    hiringHealthScore: 0,
    recommendation: 'Proceed with caution',
    geminiSummary: '',
    geminiAvailable: false,
  };

  if (googleAiApiKey && stats.totalUniqueJobs > 0) {
    try {
      log.info(`[${cmp}] Running Gemini ghost analysis…`);
      const ai = await analyzeWithGemini(googleAiApiKey, cmp, portal, stats, start, end);
      Object.assign(base, ai);
      log.info(`[${cmp}] Gemini complete`, {
        ghostRisk: base.overallGhostRisk,
        healthScore: base.hiringHealthScore,
        recommendation: base.recommendation,
      });
    } catch (err) {
      log.warning(`[${cmp}] Gemini analysis failed: ${err}`);
    }
  } else if (!googleAiApiKey) {
    base.geminiSummary = `Ghost rate: ${Math.round(stats.ghostRate * 100)}%. Avg duration: ${stats.avgDurationDays} days. ${stats.ghostCandidates} of ${stats.totalUniqueJobs} jobs scored ≥70/100.`;
  }

  await Actor.pushData(base);
  return base;
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms));
}
