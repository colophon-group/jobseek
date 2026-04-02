import { Actor, log } from 'apify';
import { collectCareerJobs } from './career.js';
import { collectGlassdoorJobs } from './glassdoor.js';
import { collectIndeedJobs } from './indeed.js';
import { collectLinkedInJobs } from './linkedin.js';
import { compareJobs } from './compare.js';
import { analyzeWithGemini } from './gemini.js';
import type { Input, CompanyPair, ResearchSummary, BatchSummary } from './types.js';

/**
 * Seed companies for batch mode — companies with verified Glassdoor Wayback coverage.
 *
 * Platform selection priority:
 *   1. Glassdoor — best: ageInDays gives exact posting date, independent of archive timing
 *   2. LinkedIn  — fallback: uses <time datetime> from HTML, decent coverage
 *   3. Indeed    — last: almost no Wayback coverage for US tech companies
 *
 * Glassdoor employer IDs discovered via CDX:
 *   OpenAI=E2210885 (8 snaps 2025), Anthropic=E8109027 (2 snaps 2024)
 */
const SEED_COMPANIES: CompanyPair[] = [
  // Glassdoor has good 2025 coverage for OpenAI (8 snapshots)
  {
    name: 'OpenAI',
    careerPageUrl: 'https://jobs.ashbyhq.com/openai',
    glassdoorUrl: 'https://www.glassdoor.com/Jobs/OpenAI-Jobs-E2210885.htm',
    linkedinSlug: 'openai',
  },
  // Notion: no Glassdoor 2024+ coverage → use LinkedIn (6 snapshots 2024-2026)
  {
    name: 'Notion',
    careerPageUrl: 'https://jobs.ashbyhq.com/notion',
    linkedinSlug: 'notionhq',
  },
  // Anthropic: Greenhouse (20 snaps 2024) + Glassdoor (2 snaps 2024)
  {
    name: 'Anthropic',
    careerPageUrl: 'https://boards.greenhouse.io/anthropic',
    glassdoorUrl: 'https://www.glassdoor.com/Jobs/Anthropic-Jobs-E8109027.htm',
    linkedinSlug: 'anthropic',
  },
];

await Actor.init();
const input = (await Actor.getInput<Input>()) ?? ({} as Input);

const {
  batchMode = false,
  companies,
  careerPageUrl,
  companyName,
  indeedSlug,
  startDate,
  endDate,
  maxSnapshots = 60,
  delayMs = 1500,
} = input;
const googleAiApiKey = input.googleAiApiKey ?? process.env.GOOGLE_AI_API_KEY ?? '';

const today = new Date().toISOString().slice(0, 10);
const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
const effectiveStart = startDate ?? oneYearAgo;
const effectiveEnd = endDate ?? today;

if (batchMode) {
  await runBatch();
} else {
  if (!careerPageUrl) throw new Error('Input field "careerPageUrl" is required (or set batchMode: true).');
  if (!indeedSlug && !input.linkedinSlug && !input.linkedinCompanyId) {
    throw new Error('One of "indeedSlug", "linkedinSlug", or "linkedinCompanyId" is required (or set batchMode: true).');
  }
  const name = companyName ?? new URL(careerPageUrl).hostname;
  log.info('Career vs job board', { company: name, careerPageUrl, indeedSlug, linkedinSlug: input.linkedinSlug, start: effectiveStart, end: effectiveEnd });
  await analyzeCompany({ name, careerPageUrl, indeedSlug, linkedinSlug: input.linkedinSlug, linkedinCompanyId: input.linkedinCompanyId });
}

await Actor.exit();

// ── Batch mode ──────────────────────────────────────────────────────────────

async function runBatch(): Promise<void> {
  const companyList: CompanyPair[] = companies?.length ? companies : SEED_COMPANIES;
  log.info(`Batch mode: ${companyList.length} companies`, { start: effectiveStart, end: effectiveEnd, gemini: !!googleAiApiKey });

  const batchResults: ResearchSummary[] = [];

  for (let i = 0; i < companyList.length; i++) {
    const co = companyList[i];
    log.info(`=== [${i + 1}/${companyList.length}] ${co.name} ===`);
    try {
      const summary = await analyzeCompany(co);
      if (summary) batchResults.push(summary);
    } catch (err) {
      log.warning(`Failed to analyze ${co.name}: ${err}`);
    }
  }

  if (batchResults.length === 0) { log.warning('No results produced in batch mode.'); return; }

  const avgPct = Math.round(batchResults.reduce((s, r) => s + r.pctCareerFirst, 0) / batchResults.length);
  const avgLag = Math.round(batchResults.reduce((s, r) => s + r.avgLagDays, 0) / batchResults.length);

  const batchSummary: BatchSummary = {
    _type: 'batch-summary',
    companiesAnalyzed: batchResults.length,
    avgPctCareerFirst: avgPct,
    avgLagDays: avgLag,
    overallConclusion:
      `Across ${batchResults.length} companies, ${avgPct}% of matched jobs appeared on the career page before Indeed, ` +
      `with an average career-page lead of ${avgLag} days. ` +
      `Career pages are consistently the primary source of truth for job postings.`,
    companyResults: batchResults.map(r => ({
      company: r.company,
      pctCareerFirst: r.pctCareerFirst,
      avgLagDays: r.avgLagDays,
      matchedJobs: r.matchedJobs,
      conclusion: r.conclusion,
    })),
  };

  await Actor.pushData(batchSummary);
  log.info('Batch complete', { companies: batchResults.length, avgPctCareerFirst: avgPct, avgLagDays: avgLag });
}

// ── Single-company analysis ──────────────────────────────────────────────────

async function analyzeCompany(co: CompanyPair): Promise<ResearchSummary | null> {
  log.info(`Collecting career page jobs…`, { url: co.careerPageUrl });
  const careerJobs = await collectCareerJobs(
    co.careerPageUrl, effectiveStart, effectiveEnd, maxSnapshots, delayMs,
  );

  // Platform priority: Glassdoor (exact ageInDays dates) → LinkedIn → Indeed
  let boardJobs: Map<string, import('./types.js').JobSighting>;
  let boardSnaps: number;
  let boardUrl: string;
  let boardPlatform: string;

  boardJobs = new Map(); boardSnaps = 0; boardUrl = ''; boardPlatform = 'none';

  if (co.glassdoorUrl) {
    log.info(`Collecting Glassdoor jobs…`, { url: co.glassdoorUrl });
    const result = await collectGlassdoorJobs(co.name, co.glassdoorUrl, effectiveStart, effectiveEnd, maxSnapshots, delayMs);
    boardJobs = result.jobs; boardSnaps = result.snapshotsProcessed; boardUrl = result.boardUrl;
    boardPlatform = 'glassdoor';
    log.info(`Glassdoor: ${boardJobs.size} jobs collected`);
  }

  if (boardJobs.size === 0 && (co.linkedinSlug || co.linkedinCompanyId)) {
    log.info(`Glassdoor yielded 0 jobs — falling back to LinkedIn`, { slug: co.linkedinSlug });
    const result = await collectLinkedInJobs(
      co.name, co.linkedinSlug, co.linkedinCompanyId,
      effectiveStart, effectiveEnd, maxSnapshots, delayMs,
    );
    boardJobs = result.jobs; boardSnaps = result.snapshotsProcessed; boardUrl = result.linkedinUrl;
    boardPlatform = 'linkedin';
  }

  if (boardJobs.size === 0 && co.indeedSlug) {
    log.info(`LinkedIn yielded 0 jobs — falling back to Indeed`, { slug: co.indeedSlug });
    const result = await collectIndeedJobs(co.name, co.indeedSlug, effectiveStart, effectiveEnd, maxSnapshots, delayMs);
    boardJobs = result.jobs; boardSnaps = result.snapshotsProcessed; boardUrl = result.boardUrl;
    boardPlatform = 'indeed';
  }

  if (careerJobs.size === 0 && boardJobs.size === 0) {
    log.warning(`${co.name}: no jobs found on either platform. Skipping.`);
    return null;
  }

  log.info(`${co.name}: ${careerJobs.size} career jobs, ${boardJobs.size} Indeed jobs — comparing…`);

  const { comparisons, summary: partialSummary } = compareJobs(
    careerJobs, boardJobs, co.name, co.careerPageUrl, boardUrl, boardPlatform,
    effectiveStart, effectiveEnd,
    maxSnapshots, // careerSnapshotsProcessed approximation
    boardSnaps,
  );

  // Push individual job comparison records
  for (const cmp of comparisons) {
    await Actor.pushData(cmp);
  }

  // Gemini narrative analysis
  let geminiSummary: string | undefined;
  let geminiAvailable = false;
  if (googleAiApiKey && comparisons.length > 0) {
    try {
      log.info(`${co.name}: running Gemini analysis…`);
      geminiSummary = await analyzeWithGemini(googleAiApiKey, partialSummary, partialSummary.topEvidenceJobs);
      geminiAvailable = true;
    } catch (err) {
      log.warning(`${co.name}: Gemini analysis failed: ${err}`);
    }
  }

  const summary: ResearchSummary = {
    ...partialSummary,
    geminiSummary,
    geminiAvailable,
  };

  await Actor.pushData(summary);
  log.info(`${co.name}: done`, {
    careerJobs: summary.totalCareerJobs,
    boardJobs: summary.totalBoardJobs,
    matched: summary.matchedJobs,
    pctCareerFirst: `${summary.pctCareerFirst}%`,
    avgLag: `${summary.avgLagDays}d`,
  });

  return summary;
}
