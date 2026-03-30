/**
 * Breezy HR + iCIMS company discovery via Wayback CDX.
 *
 * BreezyHR: {slug}.breezy.hr (SMB ATS)
 * iCIMS: {slug}.icims.com (enterprise ATS, 4000+ companies incl. Fortune 500)
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

// ── BreezyHR ─────────────────────────────────────────────────────────────────

const BREEZY_RESERVED = new Set(['www', 'app', 'api', 'login', 'support', 'help', 'blog', 'static', 'assets']);

function extractBreezySlug(url: string): string | null {
  try {
    const u = new URL(url);
    // Pattern 1: {company}.breezy.hr (subdomain)
    if (u.hostname.endsWith('.breezy.hr') && u.hostname !== 'app.breezy.hr' && u.hostname !== 'www.breezy.hr') {
      const s = u.hostname.replace('.breezy.hr', '');
      return (!s || BREEZY_RESERVED.has(s) || s.length < 2) ? null : s.toLowerCase();
    }
    // Pattern 2: app.breezy.hr/p/{company} or breezy.hr/p/{company}
    if (u.hostname === 'app.breezy.hr' || u.hostname === 'breezy.hr' || u.hostname === 'www.breezy.hr') {
      const parts = u.pathname.split('/').filter(Boolean);
      if (parts[0] === 'p' && parts[1] && parts[1].length >= 2 && !BREEZY_RESERVED.has(parts[1])) {
        return parts[1].toLowerCase();
      }
    }
    return null;
  } catch { return null; }
}

export async function discoverFromBreezyHR(): Promise<CompanyDiscovery[]> {
  log.info('breezyhr: CDX wildcard discovery (subdomain + app.breezy.hr/p/* pattern)');
  // *.breezy.hr/* has 154 CDX pages — use 8 pages for better coverage
  const [subdomainSlugs, appSlugs] = await Promise.all([
    cdxEnumerateSlugs('*.breezy.hr/*', extractBreezySlug, 5000, 8),
    cdxEnumerateSlugs('app.breezy.hr/p/*/*', extractBreezySlug, 3000, 4),
  ]);
  const merged = new Map(subdomainSlugs);
  for (const [k, v] of appSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`breezyhr: ${merged.size} unique company slugs`);
  return merged.size ? slugsToDiscoveries(merged, s => `https://${s}.breezy.hr`, 'breezyhr') : [];
}

// ── iCIMS ─────────────────────────────────────────────────────────────────────

const ICIMS_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'portal', 'connect', 'careers', 'talent', 'recruiting']);

function extractICIMSSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.icims.com')) return null;
    // Strip common prefixes: careers-company.icims.com → company
    let s = u.hostname.replace('.icims.com', '').toLowerCase();
    s = s.replace(/^(careers?[-_]|jobs[-_]|apply[-_]|talent[-_])/, '');
    if (!s || ICIMS_RESERVED.has(s) || s.length < 2) return null;
    return s;
  } catch { return null; }
}

export async function discoverFromICIMS(): Promise<CompanyDiscovery[]> {
  log.info('icims: CDX wildcard discovery — enterprise ATS with 4000+ companies');
  // *.icims.com/jobs* has 1325 CDX pages — use 15 pages for significantly better coverage
  const slugs = await cdxEnumerateSlugs('*.icims.com/jobs*', extractICIMSSlug, 10000, 15);
  log.info(`icims: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.icims.com/jobs/search`, 'icims');
}

// ── Freshteam (Freshworks ATS) ────────────────────────────────────────────────
// Popular ATS used by global mid-size companies, strong in Asia/APAC and tech.
// Pattern: {company}.freshteam.com/jobs

const FRESHTEAM_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'status', 'docs']);

function extractFreshteamSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.freshteam.com')) return null;
    const s = u.hostname.replace('.freshteam.com', '').toLowerCase();
    return (!s || FRESHTEAM_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromFreshteam(): Promise<CompanyDiscovery[]> {
  log.info('freshteam: CDX discovery — Freshworks ATS (global, strong in Asia/APAC)');
  // *.freshteam.com/jobs* has 18 CDX pages — use 6 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.freshteam.com/jobs*', extractFreshteamSlug, 5000, 6);
  log.info(`freshteam: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.freshteam.com/jobs`, 'freshteam');
}

// ── Homerun (Dutch/EU ATS) ────────────────────────────────────────────────────
// Growing ATS popular in Netherlands, Belgium, and broader EU startup scene.
// Pattern: {company}.homerun.co

const HOMERUN_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo']);

function extractHomerunSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.homerun.co')) return null;
    const s = u.hostname.replace('.homerun.co', '').toLowerCase();
    return (!s || HOMERUN_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromHomerun(): Promise<CompanyDiscovery[]> {
  log.info('homerun: CDX discovery — Dutch/EU ATS ({company}.homerun.co)');
  // *.homerun.co/* has 24 CDX pages — use 6 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.homerun.co/*', extractHomerunSlug, 3000, 6);
  log.info(`homerun: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.homerun.co`, 'homerun');
}

// ── HiBob (modern HRIS/ATS) ────────────────────────────────────────────────────
// Used by JetBrains, monday.com, Wix, Papaya Global, Pleo, Lightspeed.
// Pattern: app.hibob.com/careers/{company}

const HIBOB_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'platform', 'pricing', 'features', 'about']);

function extractHiBobSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'app.hibob.com') return null;
    const parts = u.pathname.split('/').filter(Boolean);
    if (parts[0] !== 'careers') return null;
    const seg = parts[1];
    return (!seg || HIBOB_RESERVED.has(seg.toLowerCase()) || seg.length < 2) ? null : seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromHiBob(): Promise<CompanyDiscovery[]> {
  log.info('hibob: CDX discovery — modern HRIS/ATS (JetBrains, monday.com, Wix, Pleo, etc.)');
  const slugs = await cdxEnumerateSlugs('app.hibob.com/careers/*/*', extractHiBobSlug, 3000);
  log.info(`hibob: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://app.hibob.com/careers/${s}`, 'hibob');
}

// ── Hireology ─────────────────────────────────────────────────────────────────
// Automotive/franchise/retail ATS used by car dealerships, franchises, and healthcare.
// Pattern: {company}.hireology.com/jobs or {company}.hireology.com/careers

const HIREOLOGY_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'portal', 'recruiting']);

function extractHireologySlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.hireology.com')) return null;
    const s = u.hostname.replace('.hireology.com', '').toLowerCase();
    return (!s || HIREOLOGY_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromHireology(): Promise<CompanyDiscovery[]> {
  log.info('hireology: CDX discovery — automotive/franchise/retail ATS');
  // *.hireology.com/jobs* has 36 CDX pages — use 8 pages for better coverage
  const [jobsSlugs, careersSlugs] = await Promise.all([
    cdxEnumerateSlugs('*.hireology.com/jobs*', extractHireologySlug, 4000, 8),
    cdxEnumerateSlugs('*.hireology.com/careers*', extractHireologySlug, 2000, 4),
  ]);
  const merged = new Map(jobsSlugs);
  for (const [k, v] of careersSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`hireology: ${merged.size} unique company slugs`);
  if (!merged.size) return [];
  return slugsToDiscoveries(merged, s => `https://${s}.hireology.com/jobs`, 'hireology');
}

// ── Zoho Recruit ──────────────────────────────────────────────────────────────
// Cloud-based ATS from Zoho used by SMBs worldwide.
// Pattern: {company}.zohorecruit.com/jobs/Careers

const ZOHO_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'eu', 'in', 'au']);

function extractZohoRecruitSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.zohorecruit.com')) return null;
    const s = u.hostname.replace('.zohorecruit.com', '').toLowerCase();
    // Skip region prefixes
    if (/^(eu|in|au|us|ca)$/.test(s)) return null;
    return (!s || ZOHO_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromZohoRecruit(): Promise<CompanyDiscovery[]> {
  log.info('zohorecruit: CDX discovery — Zoho cloud ATS (global SMBs)');
  // *.zohorecruit.com/jobs/* has 819 CDX pages — use 12 pages for significantly better coverage
  const slugs = await cdxEnumerateSlugs('*.zohorecruit.com/jobs/*', extractZohoRecruitSlug, 4000, 12);
  log.info(`zohorecruit: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.zohorecruit.com/jobs/Careers`, 'zohorecruit');
}

// ── Darwinbox ─────────────────────────────────────────────────────────────────
// India enterprise HCM/ATS used by 700+ enterprises: Swiggy, Zomato, Puma, JSW, Bajaj.
// Pattern: {company}.darwinbox.com/ms/candidate/jobs

const DARWINBOX_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'hr', 'dev', 'staging', 'uat']);

function extractDarwinboxSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.darwinbox.com') && !u.hostname.endsWith('.darwinbox.in')) return null;
    const s = u.hostname.replace(/\.(darwinbox\.com|darwinbox\.in)$/, '').toLowerCase();
    return (!s || DARWINBOX_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromDarwinbox(): Promise<CompanyDiscovery[]> {
  log.info('darwinbox: CDX discovery — India enterprise HCM/ATS (Swiggy, Zomato, Puma, JSW)');
  const [comSlugs, inSlugs] = await Promise.all([
    cdxEnumerateSlugs('*.darwinbox.com/ms/candidate/jobs*', extractDarwinboxSlug, 5000, 8),
    cdxEnumerateSlugs('*.darwinbox.in/ms/candidate/jobs*', extractDarwinboxSlug, 2000, 4),
  ]);
  const merged = new Map(comSlugs);
  for (const [k, v] of inSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`darwinbox: ${merged.size} unique company slugs`);
  if (!merged.size) return [];
  return slugsToDiscoveries(merged, s => `https://${s}.darwinbox.com/ms/candidate/jobs`, 'darwinbox');
}

// ── Keka ──────────────────────────────────────────────────────────────────────
// India-origin HR platform with ATS module, strong in mid-market APAC.
// Pattern: {company}.keka.com/careers

const KEKA_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'hr', 'blog', 'status', 'docs']);

function extractKekaSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.keka.com')) return null;
    const s = u.hostname.replace('.keka.com', '').toLowerCase();
    return (!s || KEKA_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromKeka(): Promise<CompanyDiscovery[]> {
  log.info('keka: CDX discovery — India HR/ATS platform (mid-market APAC)');
  const slugs = await cdxEnumerateSlugs('*.keka.com/careers*', extractKekaSlug, 5000, 8);
  log.info(`keka: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.keka.com/careers`, 'keka');
}
