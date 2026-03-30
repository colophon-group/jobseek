import { Actor, log } from 'apify';
import { fetchCdxSnapshots } from './cdx.js';
import { fetchArchivedPage, checkHiringCafeSignal } from './fetch.js';
import { extractJobs } from './extractors/index.js';
import { buildJobRegistry, computeGhostStats, registryToTimeline } from './ghost.js';
import { fetchUrlInventory, filterJobUrls, buildJobRecords } from './inventory.js';
import { analyzeWithGemini, discoverGhostCompanies } from './gemini.js';
import type { Input, CompanyInput, DayResult, SummaryRecord, GhostAnalysis, JobRecord, TimelinePoint, BatchSummaryRecord } from './types.js';

const SEED_COMPANIES: CompanyInput[] = [
  { name: 'Coinbase',      portalUrl: 'https://boards.greenhouse.io/coinbase',      inventoryMode: false },
  { name: 'Lyft',          portalUrl: 'https://boards.greenhouse.io/lyft',          inventoryMode: false },
  { name: 'Figma',         portalUrl: 'https://boards.greenhouse.io/figma',         inventoryMode: false },
  { name: 'Notion',        portalUrl: 'https://boards.greenhouse.io/notion',        inventoryMode: false },
  { name: 'OpenAI',        portalUrl: 'https://boards.greenhouse.io/openai',        inventoryMode: false },
  { name: 'Twilio',        portalUrl: 'https://boards.greenhouse.io/twilio',        inventoryMode: false },
  { name: 'Anthropic',     portalUrl: 'https://boards.greenhouse.io/anthropic',     inventoryMode: false },
  { name: 'Scale AI',      portalUrl: 'https://jobs.lever.co/scaleai',              inventoryMode: false },
  { name: 'Brex',          portalUrl: 'https://jobs.lever.co/brex',                 inventoryMode: false },
  { name: 'Rippling',      portalUrl: 'https://jobs.lever.co/rippling',             inventoryMode: false },
  { name: 'Linear',        portalUrl: 'https://jobs.ashbyhq.com/linear',            inventoryMode: false },
  { name: 'Vercel',        portalUrl: 'https://jobs.ashbyhq.com/vercel',            inventoryMode: false },
  { name: 'Retool',        portalUrl: 'https://jobs.ashbyhq.com/retool',            inventoryMode: false },
  { name: 'Cursor',        portalUrl: 'https://jobs.ashbyhq.com/anysphere',         inventoryMode: false },
  { name: 'Loom',          portalUrl: 'https://jobs.ashbyhq.com/loom',              inventoryMode: false },
  { name: 'Deloitte',      portalUrl: 'https://boards.greenhouse.io/deloitte',      inventoryMode: false },
  { name: 'PwC',           portalUrl: 'https://boards.greenhouse.io/pwc',           inventoryMode: false },
  { name: 'Booz Allen',    portalUrl: 'https://jobs.lever.co/boozallen',            inventoryMode: false },
  { name: 'Robinhood',     portalUrl: 'https://boards.greenhouse.io/robinhood',     inventoryMode: false },
  { name: 'Stripe',        portalUrl: 'https://boards.greenhouse.io/stripe',        inventoryMode: false },
  { name: 'Bosch',         portalUrl: 'https://jobs.smartrecruiters.com/BoschGroup',    inventoryMode: false },
  { name: 'Sephora',       portalUrl: 'https://jobs.smartrecruiters.com/SephoraUSA',    inventoryMode: false },
  { name: "McDonald's",    portalUrl: 'https://jobs.smartrecruiters.com/McDonalds',     inventoryMode: false },
  { name: 'Lidl',          portalUrl: 'https://jobs.smartrecruiters.com/Lidl',          inventoryMode: false },
  { name: 'WP Engine',     portalUrl: 'https://wpengine.bamboohr.com/jobs',             inventoryMode: false },
  { name: 'Qualtrics',     portalUrl: 'https://qualtrics.bamboohr.com/jobs',            inventoryMode: false },
  { name: 'Canopy',        portalUrl: 'https://canopy.bamboohr.com/jobs',               inventoryMode: false },
  { name: 'Miro',          portalUrl: 'https://miro.recruitee.com',                     inventoryMode: false },
  { name: 'Typeform',      portalUrl: 'https://typeform.recruitee.com',                 inventoryMode: false },
  { name: 'Klarna',        portalUrl: 'https://boards.greenhouse.io/klarna',            inventoryMode: false },
  { name: 'N26',           portalUrl: 'https://boards.greenhouse.io/n26',               inventoryMode: false },
  { name: 'Gorillas',      portalUrl: 'https://boards.greenhouse.io/gorillas',          inventoryMode: false },
  { name: 'Klarna (SE)',   portalUrl: 'https://klarna.teamtailor.com/jobs',             inventoryMode: false },
  { name: 'Infosys',       portalUrl: 'https://career.infosys.com/joblist',             inventoryMode: true  },
  { name: 'Wipro',         portalUrl: 'https://careers.wipro.com/careers-home',         inventoryMode: true  },
  { name: 'Amazon',        portalUrl: 'https://careers-us-amazon.icims.com/jobs/search', inventoryMode: false },
  { name: 'Dell',          portalUrl: 'https://dell.icims.com/jobs/search',              inventoryMode: false },
  { name: 'FedEx',         portalUrl: 'https://careers-fedex.icims.com/jobs/search',    inventoryMode: false },
  { name: 'Boeing',        portalUrl: 'https://boeing.taleo.net/careersection/2/jobsearch.ftl',  inventoryMode: false },
  { name: 'Lockheed',      portalUrl: 'https://lmco.taleo.net/careersection/2/jobsearch.ftl',    inventoryMode: false },
  { name: 'Hulu',          portalUrl: 'https://hulu.jobvite.com/careers',              inventoryMode: false },
  { name: 'Siemens',       portalUrl: 'https://jobs.siemens.com/careers',              inventoryMode: true  },
  // Swiss / European companies
  { name: 'Novartis',      portalUrl: 'https://jobs.smartrecruiters.com/Novartis',     inventoryMode: false },
  { name: 'Nestlé',        portalUrl: 'https://jobs.smartrecruiters.com/Nestle1',      inventoryMode: false },
  { name: 'ABB',           portalUrl: 'https://jobs.smartrecruiters.com/ABBLtd',       inventoryMode: false },
  { name: 'Roche',         portalUrl: 'https://jobs.smartrecruiters.com/RocheHolding', inventoryMode: false },
  { name: 'Zurich Insurance', portalUrl: 'https://jobs.smartrecruiters.com/ZurichInsuranceGroup', inventoryMode: false },
  { name: 'UBS',           portalUrl: 'https://jobs.smartrecruiters.com/UBSAGb',       inventoryMode: false },
  { name: 'Adecco',        portalUrl: 'https://jobs.smartrecruiters.com/AdeccoGroup',  inventoryMode: false },
  { name: 'Allianz',       portalUrl: 'https://jobs.smartrecruiters.com/Allianz',      inventoryMode: false },
  { name: 'SAP',           portalUrl: 'https://jobs.smartrecruiters.com/SAP',          inventoryMode: false },
  { name: 'BASF',          portalUrl: 'https://jobs.smartrecruiters.com/BASF',         inventoryMode: false },
  { name: 'Spotify',       portalUrl: 'https://boards.greenhouse.io/spotify',          inventoryMode: false },
  { name: 'Zalando',       portalUrl: 'https://jobs.zalando.com/en/jobs',              inventoryMode: true  },
  // SuccessFactors (SAP ATS) — European enterprises
  { name: 'Continental',  portalUrl: 'https://continental-jobs.successfactors.com/careers', inventoryMode: true  },
  { name: 'Bayer',        portalUrl: 'https://career.bayer.com/en/',                  inventoryMode: true  },
  { name: 'Deutsche Bank', portalUrl: 'https://www.db.com/careers/en/grad/job-search/', inventoryMode: true  },
  // Pinpoint ATS — UK/EU startup ATS
  { name: 'Monzo',        portalUrl: 'https://monzo.com/careers',                     inventoryMode: true  },
  // BambooHR EU companies
  { name: 'Personio',     portalUrl: 'https://personio.bamboohr.com/jobs',             inventoryMode: false },
  { name: 'Celonis',      portalUrl: 'https://celonis.bamboohr.com/jobs',              inventoryMode: false },
  { name: 'Contentful',   portalUrl: 'https://contentful.bamboohr.com/jobs',           inventoryMode: false },
  { name: 'GetYourGuide', portalUrl: 'https://boards.greenhouse.io/getyourguide',      inventoryMode: false },
  { name: 'Moonpay',      portalUrl: 'https://boards.greenhouse.io/moonpay',           inventoryMode: false },
  // Rippling ATS — fast-growing tech companies
  { name: 'Notion (Rippling)', portalUrl: 'https://ats.rippling.com/notion',           inventoryMode: false },
  { name: 'Ramp',         portalUrl: 'https://ats.rippling.com/ramp',                  inventoryMode: false },
  { name: 'Brex',         portalUrl: 'https://ats.rippling.com/brex',                  inventoryMode: false },
  // Fountain ATS — gig economy / shift work
  { name: 'DoorDash',     portalUrl: 'https://jobs.fountain.com/doordash',              inventoryMode: false },
  { name: 'Instacart',    portalUrl: 'https://jobs.fountain.com/instacart',             inventoryMode: false },
];

await Actor.init();
const input = (await Actor.getInput<Input>()) ?? ({} as Input);
const { batchMode=false, companies, discoveryRounds=2, portalUrl, startDate, endDate, maxSnapshots=100, delayMs=1500, inventoryMode=false, companyName } = input;
const googleAiApiKey = input.googleAiApiKey ?? process.env.GOOGLE_AI_API_KEY ?? '';
const today = new Date().toISOString().slice(0, 10);
const oneYearAgo = new Date(Date.now() - 365*24*60*60*1000).toISOString().slice(0, 10);
const effectiveStart = startDate ?? oneYearAgo;
const effectiveEnd = endDate ?? today;

if (batchMode) { await runBatch(); await Actor.exit(); }
if (!portalUrl) throw new Error('Input field "portalUrl" is required (or set batchMode: true).');
const company = companyName ?? new URL(portalUrl).hostname;
log.info('Wayback Job History', { portalUrl, company, mode: inventoryMode ? 'inventory' : 'snapshot', start: effectiveStart, end: effectiveEnd, gemini: !!googleAiApiKey });
await analyzeCompany(company, portalUrl, inventoryMode);
await Actor.exit();

async function runBatch() {
  const companyList: CompanyInput[] = companies?.length ? companies : SEED_COMPANIES;
  log.info(`Batch mode: ${companyList.length} seed companies, up to ${discoveryRounds} discovery rounds`, { gemini: !!googleAiApiKey, start: effectiveStart, end: effectiveEnd });
  const processedUrls = new Set<string>();
  let queue: CompanyInput[] = [...companyList];
  const allResults: GhostAnalysis[] = [];
  for (let round = 1; round <= discoveryRounds; round++) {
    const toProcess = queue.filter(c => !processedUrls.has(c.portalUrl));
    if (toProcess.length === 0) { log.info(`Round ${round}: no new companies, stopping`); break; }
    log.info(`=== Round ${round} — ${toProcess.length} companies ===`);
    const roundResults: GhostAnalysis[] = [];
    for (const co of toProcess) {
      processedUrls.add(co.portalUrl);
      log.info(`Analyzing: ${co.name}`, { portalUrl: co.portalUrl, inventoryMode: co.inventoryMode });
      try {
        const analysis = await analyzeCompany(co.name, co.portalUrl, co.inventoryMode ?? false);
        if (analysis) { roundResults.push(analysis); allResults.push(analysis); }
      } catch (err) { log.warning(`Failed to analyze ${co.name}: ${err}`); }
    }
    let discoveredCompanies: CompanyInput[] = [];
    if (round < discoveryRounds && googleAiApiKey && allResults.length > 0) {
      try {
        const suggested = await discoverGhostCompanies(googleAiApiKey, allResults, round);
        discoveredCompanies = suggested.filter(c => !processedUrls.has(c.portalUrl));
        queue = discoveredCompanies;
        log.info(`Gemini suggested ${discoveredCompanies.length} new companies for round ${round + 1}`);
      } catch (err) { log.warning(`Company discovery failed: ${err}`); }
    }
    const batchSummary: BatchSummaryRecord = {
      _type: 'batch-summary', round, companiesAnalyzed: roundResults.length,
      avgGhostRisk: roundResults.length > 0 ? Math.round(roundResults.reduce((s, r) => s + r.overallGhostRisk, 0) / roundResults.length) : 0,
      worstOffenders: [...roundResults].sort((a, b) => b.overallGhostRisk - a.overallGhostRisk).slice(0, 5).map(r => ({ company: r.company, ghostRisk: r.overallGhostRisk, recommendation: r.recommendation })),
      discoveredCompanies,
    };
    await Actor.pushData(batchSummary);
    log.info(`Round ${round} complete`, { analyzed: roundResults.length, avgGhostRisk: batchSummary.avgGhostRisk });
  }
  log.info('Batch complete', { totalCompanies: processedUrls.size, totalAnalyzed: allResults.length });
}

async function analyzeCompany(cmp: string, portal: string, useInventoryMode: boolean): Promise<GhostAnalysis | null> {
  return useInventoryMode ? runInventoryMode(cmp, portal) : runSnapshotMode(cmp, portal);
}

async function runInventoryMode(cmp: string, portal: string): Promise<GhostAnalysis | null> {
  log.info(`[${cmp}] INVENTORY mode — scanning all archived job URLs`);
  const allUrls = await fetchUrlInventory(portal, effectiveStart, effectiveEnd, maxSnapshots * 50);
  const jobUrls = filterJobUrls(allUrls);
  log.info(`[${cmp}] Filtered to ${jobUrls.length} job-page URLs from ${allUrls.length} total`);
  if (jobUrls.length === 0) { log.warning(`[${cmp}] No job URLs found. Try snapshot mode.`); return null; }
  const limit = Math.min(jobUrls.length, maxSnapshots * 2);
  const jobRecords = await buildJobRecords(jobUrls.slice(0, limit), effectiveStart, effectiveEnd, delayMs);
  for (const record of jobRecords) await Actor.pushData({ ...record, _company: cmp });
  const registry = new Map<string, JobRecord>(jobRecords.map(r => [r.url, r]));
  const stats = computeGhostStats(registry);
  const timeline = registryToTimeline(registry);
  const analysis = await pushGhostAnalysis(cmp, portal, effectiveStart, effectiveEnd, stats, timeline);
  log.info(`[${cmp}] Inventory complete`, { totalJobUrls: jobRecords.length, ghostCandidates: stats.ghostCandidates, ghostRate: `${Math.round(stats.ghostRate * 100)}%`, ghostRisk: analysis.overallGhostRisk });
  return analysis;
}

async function runSnapshotMode(cmp: string, portal: string): Promise<GhostAnalysis | null> {
  log.info(`[${cmp}] SNAPSHOT mode — processing daily Wayback snapshots`);
  const snapshots = await fetchCdxSnapshots({ url: portal, startDate: effectiveStart, endDate: effectiveEnd, maxSnapshots });
  if (snapshots.length === 0) { log.warning(`[${cmp}] No Wayback snapshots found. Try inventoryMode: true.`); return null; }
  log.info(`[${cmp}] Processing ${snapshots.length} daily snapshots`);
  const dayResults: DayResult[] = [];
  const timeline: TimelinePoint[] = [];
  for (let i = 0; i < snapshots.length; i++) {
    const snap = snapshots[i];
    const date = `${snap.timestamp.slice(0,4)}-${snap.timestamp.slice(4,6)}-${snap.timestamp.slice(6,8)}`;
    log.info(`[${cmp}] [${i+1}/${snapshots.length}] ${date}`, { ts: snap.timestamp });
    const html = await fetchArchivedPage(snap.timestamp, snap.original);
    if (!html) {
      const rec: DayResult = { date, timestamp: snap.timestamp, snapshotUrl: `https://web.archive.org/web/${snap.timestamp}/${snap.original}`, jobCount: 0, jobs: [], extractionMethod: 'fetch-failed', error: 'fetch_failed' };
      await Actor.pushData({ ...rec, _company: cmp }); dayResults.push(rec); timeline.push({ date, jobCount: 0 });
      await sleep(delayMs); continue;
    }
    const { jobs, method } = await extractJobs(html, snap);
    const rec: DayResult = { date, timestamp: snap.timestamp, snapshotUrl: `https://web.archive.org/web/${snap.timestamp}/${snap.original}`, jobCount: jobs.length, jobs, extractionMethod: method };
    await Actor.pushData({ ...rec, _company: cmp }); dayResults.push(rec); timeline.push({ date, jobCount: jobs.length });
    log.info(`[${cmp}]   → ${jobs.length} jobs (${method})`);
    await sleep(delayMs);
  }
  const jobRegistry = buildJobRegistry(dayResults);
  const stats = computeGhostStats(jobRegistry);
  for (const record of jobRegistry.values()) { if (record.ghostScore >= 40) await Actor.pushData({ _type: 'job-record', _company: cmp, ...record }); }
  const withJobs = timeline.filter(t => t.jobCount > 0);
  const avgJobCount = withJobs.length > 0 ? Math.round(withJobs.reduce((s, t) => s + t.jobCount, 0) / withJobs.length) : 0;
  const peak = timeline.reduce((best, t) => (t.jobCount > best.jobCount ? t : best), { date: '', jobCount: 0 });
  const summary: SummaryRecord = { _type: 'summary', portalUrl: portal, startDate: effectiveStart, endDate: effectiveEnd, totalSnapshotsProcessed: snapshots.length, snapshotsWithJobs: withJobs.length, avgJobCount, peakDate: peak.date, peakJobCount: peak.jobCount, latestJobCount: timeline.at(-1)?.jobCount ?? 0, timeline, _company: cmp };
  await Actor.pushData(summary);
  const analysis = await pushGhostAnalysis(cmp, portal, effectiveStart, effectiveEnd, stats, registryToTimeline(jobRegistry));
  log.info(`[${cmp}] Snapshot complete`, { snapshots: snapshots.length, uniqueJobs: stats.totalUniqueJobs, ghostCandidates: stats.ghostCandidates, ghostRisk: analysis.overallGhostRisk });
  return analysis;
}

async function pushGhostAnalysis(cmp: string, portal: string, start: string, end: string, stats: ReturnType<typeof computeGhostStats>, timeline: TimelinePoint[]): Promise<GhostAnalysis> {
  const base: GhostAnalysis = { _type: 'ghost-analysis', company: cmp, portalUrl: portal, analysisDate: new Date().toISOString().slice(0, 10), periodStart: start, periodEnd: end, ...stats, overallGhostRisk: 0, topGhostRoles: [], patterns: [], hiringHealthScore: 0, recommendation: 'Proceed with caution', geminiSummary: '', geminiAvailable: false };
  const hc = await checkHiringCafeSignal(cmp).catch(() => null);
  if (hc) {
    base.hiringCafeSignal = hc;
    if (hc.signal && !base.orgGhostSignal) base.orgGhostSignal = hc.signal;
    log.info(`[${cmp}] hiring.cafe: found=${hc.found} listings=${hc.activeListings} low=${hc.lowEngagement}`);
  }
  if (googleAiApiKey && stats.totalUniqueJobs > 0) {
    try {
      log.info(`[${cmp}] Running Gemini ghost analysis…`);
      const ai = await analyzeWithGemini(googleAiApiKey, cmp, portal, stats, start, end, hc);
      Object.assign(base, ai);
      if (base.hiringCafeSignal?.signal) base.geminiSummary += `\n\nhiring.cafe: ${base.hiringCafeSignal.signal}`;
      log.info(`[${cmp}] Gemini complete`, { ghostRisk: base.overallGhostRisk, healthScore: base.hiringHealthScore, recommendation: base.recommendation });
    } catch (err) { log.warning(`[${cmp}] Gemini analysis failed: ${err}`); }
  } else if (!googleAiApiKey) {
    // Compute ghost risk heuristically when Gemini is not available
    const rateScore = Math.min(60, Math.round(stats.ghostRate * 80));
    const durationScore = stats.avgDurationDays > 180 ? 25 : stats.avgDurationDays > 90 ? 15 : stats.avgDurationDays > 60 ? 8 : 0;
    const hcPenalty = base.hiringCafeSignal?.lowEngagement ? 15 : (base.hiringCafeSignal?.found === false ? 10 : 0);
    const orgPenalty = stats.orgGhostSignal ? 10 : 0;
    const rawRisk = Math.min(100, rateScore + durationScore + hcPenalty + orgPenalty);
    base.overallGhostRisk = rawRisk;
    base.hiringHealthScore = Math.max(0, 100 - rawRisk);
    base.recommendation = rawRisk >= 70 ? 'Likely ghost posting' : rawRisk >= 40 ? 'Proceed with caution' : 'Apply confidently';
    base.geminiSummary = `Ghost rate: ${Math.round(stats.ghostRate * 100)}% (${stats.ghostCandidates}/${stats.totalUniqueJobs} jobs scored ≥70). Avg duration: ${stats.avgDurationDays} days. Risk score: ${rawRisk}/100 (no AI key).`;
    if (stats.orgGhostSignal) base.geminiSummary += ` ${stats.orgGhostSignal}.`;
    if (base.hiringCafeSignal?.signal) base.geminiSummary += `\n\nhiring.cafe: ${base.hiringCafeSignal.signal}`;
  }
  await Actor.pushData(base);
  return base;
}

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }
