/**
 * Personio + Jobvite + SAP SuccessFactors + SmartRecruiters CDX discovery.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

// ── Personio ──────────────────────────────────────────────────────────────────

function extractPersonioSlug(url: string): string | null {
  try {
    const h = new URL(url).hostname;
    if (h.endsWith('.jobs.personio.de') || h.endsWith('.jobs.personio.com')) {
      const s = h.split('.')[0]; return s && s.length >= 2 ? s.toLowerCase() : null;
    }
    return null;
  } catch { return null; }
}

export async function discoverFromPersonio(): Promise<CompanyDiscovery[]> {
  log.info('personio: CDX discovery');
  // *.jobs.personio.de/* has 32 CDX pages — use 8 pages for better coverage
  const [de, com] = await Promise.all([
    cdxEnumerateSlugs('*.jobs.personio.de/*', extractPersonioSlug, 5000, 8),
    cdxEnumerateSlugs('*.jobs.personio.com/*', extractPersonioSlug, 3000, 4),
  ]);
  const merged = new Map(de);
  for (const [k, v] of com) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`personio: ${merged.size} companies`);
  return merged.size ? slugsToDiscoveries(merged, s => `https://${s}.jobs.personio.de`, 'personio') : [];
}

// ── Jobvite ───────────────────────────────────────────────────────────────────

const JV_RESERVED = new Set(['www','api','app','jobs','login','support','careers','hire','talent','web','connect']);

function extractJobviteSlug(url: string): string | null {
  try {
    const h = new URL(url).hostname;
    if (!h.endsWith('.jobvite.com')) return null;
    const s = h.replace('.jobvite.com', '').toLowerCase();
    return (!s || JV_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromJobvite(): Promise<CompanyDiscovery[]> {
  log.info('jobvite: CDX discovery — mid-market US ATS');
  // *.jobvite.com/careers* has 1275 CDX pages — use 15 pages for significantly better coverage
  const slugs = await cdxEnumerateSlugs('*.jobvite.com/careers*', extractJobviteSlug, 8000, 15);
  log.info(`jobvite: ${slugs.size} companies`);
  return slugs.size ? slugsToDiscoveries(slugs, s => `https://${s}.jobvite.com/careers`, 'jobvite') : [];
}

// ── SAP SuccessFactors ────────────────────────────────────────────────────────

const SF_RESERVED = new Set(['www','api','app','jobs','login','support','cdn','secure','sso','hcm','preview']);

function extractSuccessFactorsSlug(url: string): string | null {
  try {
    const h = new URL(url).hostname;
    if (!h.endsWith('.successfactors.com') && !h.endsWith('.successfactors.eu')) return null;
    const s = h.split('.')[0].toLowerCase();
    return (!s || SF_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromSuccessFactors(): Promise<CompanyDiscovery[]> {
  log.info('successfactors: CDX discovery — SAP enterprise ATS');
  // *.successfactors.com/careers* has 849 CDX pages — use 12 pages for significantly better coverage
  const [com, eu] = await Promise.all([
    cdxEnumerateSlugs('*.successfactors.com/careers*', extractSuccessFactorsSlug, 8000, 12),
    cdxEnumerateSlugs('*.successfactors.eu/careers*', extractSuccessFactorsSlug, 3000, 4),
  ]);
  const merged = new Map(com);
  for (const [k, v] of eu) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`successfactors: ${merged.size} companies`);
  return merged.size ? slugsToDiscoveries(merged, s => `https://${s}.successfactors.com/careers`, 'successfactors') : [];
}

// ── SmartRecruiters ───────────────────────────────────────────────────────────

const SR_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'status', 'careers', 'widget', 'embed']);

function extractSmartRecruitersSlug(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname !== 'jobs.smartrecruiters.com') return null;
    // Path: /CompanySlug/... — first path segment is the company slug
    const seg = parsed.pathname.split('/').filter(Boolean)[0];
    if (!seg || SR_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromSmartRecruiters(): Promise<CompanyDiscovery[]> {
  log.info('smartrecruiters: CDX discovery — Fortune 500 ATS (jobs + careers, root + nested paths)');
  const [rootSlugs, nestedSlugs, careersRootSlugs, careersNestedSlugs] = await Promise.all([
    // Root-level company pages: jobs.smartrecruiters.com/{CompanySlug} (195 CDX pages, vast coverage)
    cdxEnumerateSlugs('jobs.smartrecruiters.com/*', extractSmartRecruitersSlug, 10000, 8),
    // Nested job URLs: jobs.smartrecruiters.com/{CompanySlug}/{JobId}-{Title}
    cdxEnumerateSlugs('jobs.smartrecruiters.com/*/*', extractSmartRecruitersSlug, 10000, 4),
    // careers.smartrecruiters.com root pages: 16 CDX pages — use 8 pages
    cdxEnumerateSlugs('careers.smartrecruiters.com/*', extractSmartRecruitersSlug, 5000, 8),
    cdxEnumerateSlugs('careers.smartrecruiters.com/*/*', extractSmartRecruitersSlug, 5000, 2),
  ]);
  const merged = new Map(rootSlugs);
  for (const [k, v] of nestedSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  for (const [k, v] of careersRootSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  for (const [k, v] of careersNestedSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`smartrecruiters: ${merged.size} companies`);
  return merged.size
    ? slugsToDiscoveries(merged, s => `https://jobs.smartrecruiters.com/${s}`, 'smartrecruiters')
    : [];
}

// ── Pinpoint HQ ───────────────────────────────────────────────────────────────
// UK/EU startup ATS — growing in Series A–C space. URL: app.pinpointhq.com/{company}/jobs

const PH_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo']);

function extractPinpointSlug(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname !== 'app.pinpointhq.com') return null;
    const seg = parsed.pathname.split('/').filter(Boolean)[0];
    if (!seg || PH_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromPinpoint(): Promise<CompanyDiscovery[]> {
  log.info('pinpoint: CDX discovery — UK/EU startup ATS');
  const slugs = await cdxEnumerateSlugs('app.pinpointhq.com/*/*', extractPinpointSlug, 5000);
  log.info(`pinpoint: ${slugs.size} companies`);
  return slugs.size
    ? slugsToDiscoveries(slugs, s => `https://app.pinpointhq.com/${s}/jobs`, 'pinpoint')
    : [];
}

// ── Comeet ────────────────────────────────────────────────────────────────────
// Growing ATS in Israel/EU/US tech. URL: recruiting.comeet.co/jobs/{company}

const COMEET_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin']);

function extractComeetSlug(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname !== 'recruiting.comeet.co') return null;
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parts[0] !== 'jobs' || !parts[1]) return null;
    const seg = parts[1];
    if (COMEET_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromComeet(): Promise<CompanyDiscovery[]> {
  log.info('comeet: CDX discovery — Israel/EU/US tech ATS');
  const slugs = await cdxEnumerateSlugs('recruiting.comeet.co/jobs/*/*', extractComeetSlug, 5000);
  log.info(`comeet: ${slugs.size} companies`);
  return slugs.size
    ? slugsToDiscoveries(slugs, s => `https://recruiting.comeet.co/jobs/${s}`, 'comeet')
    : [];
}

// ── Cornerstone OnDemand ──────────────────────────────────────────────────────
// Major enterprise LMS/ATS used by Fortune 500: Boeing, Adobe, FedEx,
// UnitedHealth Group, Lockheed Martin, Pfizer, AT&T, and many others.
// Pattern: {tenant}.csod.com (tenant is typically the company name/abbreviation)

const CSOD_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'cdn', 'secure', 'help', 'blog', 'email', 'info', 'test', 'staging', 'preview', 'uat', 'demo']);

function extractCornerstoneSlug(url: string): string | null {
  try {
    const h = new URL(url).hostname;
    if (!h.endsWith('.csod.com')) return null;
    const s = h.split('.')[0].toLowerCase();
    return (!s || CSOD_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromCornerstone(): Promise<CompanyDiscovery[]> {
  log.info('cornerstone: CDX discovery — enterprise ATS (Boeing, Adobe, FedEx, UnitedHealth, Lockheed Martin)');
  // *.csod.com/careers* has 258 CDX pages — use 12 pages for significantly better coverage
  const [careerSlugs, recruitSlugs] = await Promise.all([
    cdxEnumerateSlugs('*.csod.com/careers*', extractCornerstoneSlug, 8000, 12),
    cdxEnumerateSlugs('*.csod.com/recruitmentcommon*', extractCornerstoneSlug, 5000, 4),
  ]);
  const merged = new Map(careerSlugs);
  for (const [k, v] of recruitSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`cornerstone: ${merged.size} unique tenant slugs`);
  if (!merged.size) return [];
  return slugsToDiscoveries(merged, s => `https://${s}.csod.com/careers`, 'cornerstone');
}
