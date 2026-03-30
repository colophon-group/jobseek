/**
 * Factorial HR + Kenjo CDX discovery.
 *
 * Factorial HR: fast-growing EU/LATAM HCM/ATS (Spanish-origin, used widely in Spain, LATAM, Italy, Germany).
 * Pattern: api.factorialhr.com/job_postings/{company} or {company}.factorial.co/jobs
 * Public job boards: factorialhr.com/job_postings/{company}
 *
 * Kenjo: European ATS used in DACH, Spain, and UK.
 * Pattern: app.kenjo.io/{company}/jobs
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

// ── Factorial HR ──────────────────────────────────────────────────────────────

const FACTORIAL_RESERVED = new Set([
  'www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog',
  'admin', 'careers', 'demo', 'about', 'terms', 'privacy', 'pricing',
  'es', 'en', 'de', 'fr', 'it', 'pt',
]);

function extractFactorialSlug(url: string): string | null {
  try {
    const u = new URL(url);
    // factorialhr.com/job_postings/{slug} or factorialhr.com/en/job_postings/{slug}
    if (u.hostname !== 'factorialhr.com' && u.hostname !== 'www.factorialhr.com') return null;
    const parts = u.pathname.split('/').filter(Boolean);
    // Skip locale prefix: /en/job_postings/... → look for job_postings segment
    const jpIdx = parts.indexOf('job_postings');
    if (jpIdx === -1) return null;
    const seg = parts[jpIdx + 1];
    if (!seg || FACTORIAL_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromFactorial(): Promise<CompanyDiscovery[]> {
  log.info('factorial: CDX discovery — EU/LATAM HCM/ATS');
  const slugs = await cdxEnumerateSlugs(
    'factorialhr.com/job_postings/*',
    extractFactorialSlug,
    5000,
  );
  log.info(`factorial: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://factorialhr.com/job_postings/${s}`, 'factorial');
}

// ── Kenjo ──────────────────────────────────────────────────────────────────────

const KENJO_RESERVED = new Set([
  'www', 'api', 'app', 'login', 'support', 'help', 'blog', 'admin', 'demo',
]);

function extractKenjoSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'app.kenjo.io') return null;
    const parts = u.pathname.split('/').filter(Boolean);
    const seg = parts[0];
    if (!seg || KENJO_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    // Skip "jobs" as a company slug (it's the path segment, not the company)
    if (seg === 'jobs') return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromKenjo(): Promise<CompanyDiscovery[]> {
  log.info('kenjo: CDX discovery — European ATS (DACH, Spain, UK)');
  const slugs = await cdxEnumerateSlugs(
    'app.kenjo.io/*/jobs',
    extractKenjoSlug,
    3000,
  );
  log.info(`kenjo: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://app.kenjo.io/${s}/jobs`, 'kenjo');
}

// ── Workstream ────────────────────────────────────────────────────────────────
// Hourly/shift-work ATS used by restaurant chains and retailers: Chick-fil-A, McDonald's, etc.
// Pattern: jobs.workstream.us/{company}

const WORKSTREAM_RESERVED = new Set([
  'www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog',
  'admin', 'careers', 'demo', 'about', 'terms', 'privacy', 'auth',
  'apply', 'referral', 'embed', 'widget', 'v1', 'v2',
]);

function extractWorkstreamSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'jobs.workstream.us') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || WORKSTREAM_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg) || /^[0-9a-f]{8}-/.test(seg)) return null; // skip IDs/UUIDs
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromWorkstream(): Promise<CompanyDiscovery[]> {
  log.info('workstream: CDX discovery — hourly/shift-work ATS (restaurant chains, retailers)');
  const slugs = await cdxEnumerateSlugs(
    'jobs.workstream.us/*/*',
    extractWorkstreamSlug,
    5000,
  );
  log.info(`workstream: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://jobs.workstream.us/${s}`, 'workstream');
}

// ── Dover ─────────────────────────────────────────────────────────────────────
// Modern recruiting ATS for VC-backed startups and tech companies.
// Pattern: talent.dover.com/jobs/{company} or talent.dover.com/{company}

const DOVER_RESERVED = new Set([
  'www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog',
  'admin', 'careers', 'demo', 'about', 'terms', 'privacy', 'auth', 'apply',
]);

function extractDoverSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'talent.dover.com' && u.hostname !== 'app.dover.com') return null;
    const parts = u.pathname.split('/').filter(Boolean);
    // talent.dover.com/jobs/{company} or talent.dover.com/{company}
    let seg: string | undefined;
    if (parts[0] === 'jobs' && parts[1]) {
      seg = parts[1];
    } else if (parts[0] && parts[0] !== 'jobs') {
      seg = parts[0];
    }
    if (!seg || DOVER_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg) || /^[0-9a-f]{8}-/.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromDover(): Promise<CompanyDiscovery[]> {
  log.info('dover: CDX discovery — VC-backed startup ATS');
  const [talentSlugs, appSlugs] = await Promise.all([
    cdxEnumerateSlugs('talent.dover.com/jobs/*/*', extractDoverSlug, 3000),
    cdxEnumerateSlugs('app.dover.com/jobs/*/*', extractDoverSlug, 2000),
  ]);
  const merged = new Map(talentSlugs);
  for (const [k, v] of appSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`dover: ${merged.size} unique company slugs`);
  if (!merged.size) return [];
  return slugsToDiscoveries(merged, s => `https://talent.dover.com/jobs/${s}`, 'dover');
}

// ── Jobteaser ─────────────────────────────────────────────────────────────────
// Major European campus/student job board — popular in France, Germany, Spain, and across EU.
// Pattern: jobteaser.com/{locale}/company/{slug}/jobs

const JOBTEASER_RESERVED = new Set([
  'www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog',
  'admin', 'careers', 'demo', 'about', 'en', 'de', 'fr', 'es', 'it', 'nl', 'pt',
]);

function extractJobteaserSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'jobteaser.com' && u.hostname !== 'www.jobteaser.com') return null;
    const parts = u.pathname.split('/').filter(Boolean);
    // /en/company/{slug}/jobs or /company/{slug}/jobs or /de/company/{slug}
    const companyIdx = parts.indexOf('company');
    if (companyIdx === -1) return null;
    const seg = parts[companyIdx + 1];
    if (!seg || JOBTEASER_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg)) return null; // skip pure numeric IDs
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromJobteaser(): Promise<CompanyDiscovery[]> {
  log.info('jobteaser: CDX discovery — European campus/student job board');
  const [enSlugs, deSlugs, frSlugs] = await Promise.all([
    cdxEnumerateSlugs('jobteaser.com/en/company/*/*', extractJobteaserSlug, 5000),
    cdxEnumerateSlugs('jobteaser.com/de/company/*/*', extractJobteaserSlug, 3000),
    cdxEnumerateSlugs('jobteaser.com/fr/company/*/*', extractJobteaserSlug, 3000),
  ]);
  const merged = new Map(enSlugs);
  for (const [k, v] of deSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  for (const [k, v] of frSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`jobteaser: ${merged.size} unique company slugs`);
  if (!merged.size) return [];
  return slugsToDiscoveries(merged, s => `https://jobteaser.com/en/company/${s}/jobs`, 'jobteaser');
}

// ── Eightfold.ai ──────────────────────────────────────────────────────────────
// AI talent intelligence platform used by Fortune 500 enterprises:
// Prudential, Chevron, Koch Industries, Bayer, Bristol Myers Squibb, NTT Data, Booz Allen Hamilton.
// Pattern: careers.eightfold.ai/{company}

const EIGHTFOLD_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'about', 'careers', 'apply', 'search', 'v1', 'v2', 'demo']);

function extractEightfoldSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'careers.eightfold.ai') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || EIGHTFOLD_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromEightfold(): Promise<CompanyDiscovery[]> {
  log.info('eightfold: CDX discovery — AI talent platform (Prudential, Chevron, Koch, Bayer, NTT Data)');
  const slugs = await cdxEnumerateSlugs('careers.eightfold.ai/*/*', extractEightfoldSlug, 5000);
  log.info(`eightfold: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://careers.eightfold.ai/${s}`, 'eightfold');
}

// ── PageUp ─────────────────────────────────────────────────────────────────────
// APAC enterprise ATS used by Australian/NZ governments, universities, and corporates:
// Qantas, ANZ Bank, Telstra, NAB, BHP, Woolworths Group, Government of NSW, University of Melbourne.
// Pattern: jobs.pageuppeople.com/{company}/go/

const PAGEUP_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'demo', 'go', 'search', 'apply', 'v1', 'v2']);

function extractPageUpSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'jobs.pageuppeople.com') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || PAGEUP_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromPageUp(): Promise<CompanyDiscovery[]> {
  log.info('pageup: CDX discovery — APAC enterprise ATS (Qantas, ANZ, BHP, Telstra, universities)');
  const slugs = await cdxEnumerateSlugs('jobs.pageuppeople.com/*/go/*', extractPageUpSlug, 6000);
  log.info(`pageup: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://jobs.pageuppeople.com/${s}/go/All-Jobs`, 'pageup');
}

// ── Avature ────────────────────────────────────────────────────────────────────
// Enterprise talent acquisition platform used by Amazon, LinkedIn, EY, PwC, Deloitte, NASA, and many Fortune 500.
// Pattern: careers.avature.net/{company}

const AVATURE_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'demo', 'careers', 'search', 'apply', 'about']);

function extractAvatureSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'careers.avature.net') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || AVATURE_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromAvature(): Promise<CompanyDiscovery[]> {
  log.info('avature: CDX discovery — Fortune 500 talent acquisition platform (Amazon, EY, PwC, NASA)');
  const slugs = await cdxEnumerateSlugs('careers.avature.net/*/*', extractAvatureSlug, 5000);
  log.info(`avature: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://careers.avature.net/${s}`, 'avature');
}

// ── Paycor ─────────────────────────────────────────────────────────────────────
// US payroll/HCM software with integrated ATS (formerly Newton Software).
// Used by SMBs and mid-market companies across healthcare, retail, and manufacturing.
// Pattern: {company}.paycor.com/career-portal

const PAYCOR_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'my', 'secure', 'corp']);

function extractPaycorSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.paycor.com')) return null;
    const s = u.hostname.replace('.paycor.com', '').toLowerCase();
    return (!s || PAYCOR_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromPaycor(): Promise<CompanyDiscovery[]> {
  log.info('paycor: CDX discovery — US HCM/ATS (healthcare, retail, manufacturing SMBs)');
  // *.paycor.com/career-portal* has 36 CDX pages — use 8 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.paycor.com/career-portal*', extractPaycorSlug, 5000, 8);
  log.info(`paycor: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.paycor.com/career-portal`, 'paycor');
}

// ── ClearCompany ───────────────────────────────────────────────────────────────
// US mid-market ATS used by healthcare, education, and growth-stage companies.
// Pattern: {company}.clearcompany.com/careers

const CLEARCO_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'secure', 'my']);

function extractClearCompanySlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.clearcompany.com')) return null;
    const s = u.hostname.replace('.clearcompany.com', '').toLowerCase();
    return (!s || CLEARCO_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromClearCompany(): Promise<CompanyDiscovery[]> {
  log.info('clearcompany: CDX discovery — US mid-market ATS (healthcare, education, growth-stage)');
  // *.clearcompany.com/careers* has 30 CDX pages — use 8 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.clearcompany.com/careers*', extractClearCompanySlug, 4000, 8);
  log.info(`clearcompany: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.clearcompany.com/careers`, 'clearcompany');
}

// ── Dayforce HCM (Ceridian) ────────────────────────────────────────────────────
// Major enterprise HCM/ATS used by large enterprises: Canada Goose, Dollar General,
// Lands' End, Trader Joe's, Walgreens, and 5000+ companies across North America.
// Pattern: www.dayforcehcm.com/CandidatePortal/{locale}/{tenant}

const DAYFORCE_RESERVED = new Set([
  'Content', 'api', 'scripts', 'styles', 'images', 'img', 'fonts', 'js', 'css',
  'en-us', 'en-ca', 'fr-ca', 'es-us', 'en-au', 'en-gb',
  'login', 'support', 'help', 'admin', 'demo', 'www', 'app',
]);

function extractDayforceSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'www.dayforcehcm.com') return null;
    const parts = u.pathname.split('/').filter(Boolean);
    // /CandidatePortal/{locale}/{tenant}/...
    if (parts[0] !== 'CandidatePortal' || !parts[1] || !parts[2]) return null;
    // Skip asset paths like /CandidatePortal/Content/...
    if (DAYFORCE_RESERVED.has(parts[1])) return null;
    const tenant = parts[2].toLowerCase();
    if (!tenant || DAYFORCE_RESERVED.has(tenant) || tenant.length < 2) return null;
    // Skip pure numeric or UUID-like slugs
    if (/^\d+$/.test(tenant) || /^[0-9a-f]{8}-/.test(tenant)) return null;
    return tenant;
  } catch { return null; }
}

export async function discoverFromDayforce(): Promise<CompanyDiscovery[]> {
  log.info('dayforce: CDX discovery — Ceridian enterprise HCM/ATS (Dollar General, Walgreens, 5000+ companies)');
  // www.dayforcehcm.com/CandidatePortal/* has 5 CDX pages
  const slugs = await cdxEnumerateSlugs(
    'www.dayforcehcm.com/CandidatePortal/*/*',
    extractDayforceSlug,
    5000, 5,
  );
  log.info(`dayforce: ${slugs.size} unique tenant slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(
    slugs,
    s => `https://www.dayforcehcm.com/CandidatePortal/en-US/${s}`,
    'dayforce',
  );
}
