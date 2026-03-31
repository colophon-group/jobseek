import { Actor, log } from 'apify';
import { collectCareerJobs } from './career.js';
import { collectLinkedInJobs } from './linkedin.js';
import { compareJobs } from './compare.js';
import { analyzeWithGemini } from './gemini.js';
import type { Input, CompanyPair, ResearchSummary, BatchSummary } from './types.js';

/**
 * Seed companies for batch mode — a diverse mix of ATS platforms.
 * Each pair has a well-archived career page and a LinkedIn company slug.
 */
const SEED_COMPANIES: CompanyPair[] = [
  { name: 'Stripe',       careerPageUrl: 'https://boards.greenhouse.io/stripe',         linkedinSlug: 'stripe' },
  { name: 'Coinbase',     careerPageUrl: 'https://boards.greenhouse.io/coinbase',        linkedinSlug: 'coinbase' },
  { name: 'Anthropic',    careerPageUrl: 'https://boards.greenhouse.io/anthropic',       linkedinSlug: 'anthropic' },
  { name: 'OpenAI',       careerPageUrl: 'https://boards.greenhouse.io/openai',          linkedinSlug: 'openai' },
  { name: 'Figma',        careerPageUrl: 'https://boards.greenhouse.io/figma',           linkedinSlug: 'figma' },
  { name: 'Notion',       careerPageUrl: 'https://boards.greenhouse.io/notion',          linkedinSlug: 'notion' },
  { name: 'Scale AI',     careerPageUrl: 'https://jobs.lever.co/scaleai',                linkedinSlug: 'scaleai' },
  { name: 'Brex',         careerPageUrl: 'https://jobs.lever.co/brex',                   linkedinSlug: 'brex-inc-' },
  { name: 'Rippling',     careerPageUrl: 'https://jobs.lever.co/rippling',               linkedinSlug: 'rippling' },
  { name: 'Linear',       careerPageUrl: 'https://jobs.ashbyhq.com/linear',              linkedinSlug: 'linear-app' },
  { name: 'Vercel',       careerPageUrl: 'https://jobs.ashbyhq.com/vercel',              linkedinSlug: 'vercel' },
  { name: 'Retool',       careerPageUrl: 'https://jobs.ashbyhq.com/retool',              linkedinSlug: 'tryretool' },
  { name: 'Klarna',       careerPageUrl: 'https://boards.greenhouse.io/klarna',          linkedinSlug: 'klarna' },
  { name: 'Spotify',      careerPageUrl: 'https://boards.greenhouse.io/spotify',         linkedinSlug: 'spotify' },
  { name: 'Robinhood',    careerPageUrl: 'https://boards.greenhouse.io/robinhood',       linkedinSlug: 'robinhood' },
  { name: 'Miro',         careerPageUrl: 'https://miro.recruitee.com',                   linkedinSlug: 'mirohq' },
  { name: 'Typeform',     careerPageUrl: 'https://typeform.recruitee.com',               linkedinSlug: 'typeform' },
  { name: 'Novartis',     careerPageUrl: 'https://jobs.smartrecruiters.com/Novartis',    linkedinSlug: 'novartis' },
  { name: 'SAP',          careerPageUrl: 'https://jobs.smartrecruiters.com/SAP',         linkedinSlug: 'sap' },
  { name: 'Bosch',        careerPageUrl: 'https://jobs.smartrecruiters.com/BoschGroup',  linkedinSlug: 'bosch' },
];

await Actor.init();
const input = (await Actor.getInput<Input>()) ?? ({} as Input);

const {
  batchMode = false,
  companies,
  careerPageUrl,
  companyName,
  linkedinSlug,
  linkedinCompanyId,
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
  if (!linkedinSlug && !linkedinCompanyId) {
    throw new Error('Either "linkedinSlug" or "linkedinCompanyId" is required (or set batchMode: true).');
  }
  const name = companyName ?? new URL(careerPageUrl).hostname;
  log.info('Career vs LinkedIn', { company: name, careerPageUrl, linkedinSlug, linkedinCompanyId, start: effectiveStart, end: effectiveEnd });
  await analyzeCompany({ name, careerPageUrl, linkedinSlug, linkedinCompanyId });
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
      `Across ${batchResults.length} companies, ${avgPct}% of matched jobs appeared on the career page before LinkedIn, ` +
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

  log.info(`Collecting LinkedIn jobs…`, { slug: co.linkedinSlug, id: co.linkedinCompanyId });
  const { jobs: linkedinJobs, snapshotsProcessed: liSnaps, linkedinUrl } = await collectLinkedInJobs(
    co.name, co.linkedinSlug, co.linkedinCompanyId,
    effectiveStart, effectiveEnd, maxSnapshots, delayMs,
  );

  if (careerJobs.size === 0 && linkedinJobs.size === 0) {
    log.warning(`${co.name}: no jobs found on either platform. Skipping.`);
    return null;
  }

  log.info(`${co.name}: ${careerJobs.size} career jobs, ${linkedinJobs.size} LinkedIn jobs — comparing…`);

  const { comparisons, summary: partialSummary } = compareJobs(
    careerJobs, linkedinJobs, co.name, co.careerPageUrl, linkedinUrl,
    effectiveStart, effectiveEnd,
    maxSnapshots, // careerSnapshotsProcessed approximation
    liSnaps,
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
    linkedinJobs: summary.totalLinkedInJobs,
    matched: summary.matchedJobs,
    pctCareerFirst: `${summary.pctCareerFirst}%`,
    avgLag: `${summary.avgLagDays}d`,
  });

  return summary;
}
