/**
 * Softgarden + JOIN.com CDX discovery.
 *
 * Softgarden: popular German/DACH ATS, dominant in German-speaking markets.
 * Pattern: *.softgarden.io/jobs/* or *.softgarden.de
 *
 * JOIN.com: EU-focused startup job board.
 * Pattern: join.com/companies/{slug}/jobs
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugToName, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

const SG_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'demo', 'staging', 'test', 'dev']);

// ── Softgarden ────────────────────────────────────────────────────────────────

function extractSoftgardenSlug(url: string): string | null {
  try {
    const h = new URL(url).hostname;
    if (!h.endsWith('.softgarden.io') && !h.endsWith('.softgarden.de')) return null;
    const tenant = h.split('.')[0].toLowerCase();
    return (!tenant || SG_RESERVED.has(tenant) || tenant.length < 2) ? null : tenant;
  } catch { return null; }
}

export async function discoverFromSoftgarden(): Promise<CompanyDiscovery[]> {
  log.info('softgarden: CDX discovery — popular DACH ATS');
  // *.softgarden.io/* has 55 CDX pages — use 10 pages for better coverage
  const [io, de] = await Promise.all([
    cdxEnumerateSlugs('*.softgarden.io/*', extractSoftgardenSlug, 5000, 10),
    cdxEnumerateSlugs('*.softgarden.de/*', extractSoftgardenSlug, 2000, 6),
  ]);
  const merged = new Map(io);
  for (const [k, v] of de) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`softgarden: ${merged.size} companies`);
  return merged.size
    ? slugsToDiscoveries(merged, s => `https://${s}.softgarden.io/jobs`, 'softgarden')
    : [];
}

// ── JOIN.com ──────────────────────────────────────────────────────────────────

const JOIN_RESERVED = new Set(['www', 'api', 'app', 'blog', 'help', 'support', 'about', 'press', 'login', 'signup', 'companies', 'jobs']);

function extractJoinSlug(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (!['join.com', 'www.join.com'].includes(parsed.hostname)) return null;
    const parts = parsed.pathname.split('/').filter(Boolean);
    // /companies/{slug}/jobs or /companies/{slug}
    if (parts[0] !== 'companies') return null;
    const slug = parts[1];
    if (!slug || JOIN_RESERVED.has(slug.toLowerCase()) || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch { return null; }
}

export async function discoverFromJoin(): Promise<CompanyDiscovery[]> {
  log.info('join.com: CDX discovery — EU startup job board');
  // join.com/companies/*/jobs has 30 CDX pages — use 10 pages for better coverage
  const slugs = await cdxEnumerateSlugs('join.com/companies/*/jobs', extractJoinSlug, 5000, 10);
  log.info(`join.com: ${slugs.size} companies`);
  if (!slugs.size) return [];

  const results = slugsToDiscoveries(slugs, s => `https://join.com/companies/${s}/jobs`, 'join');
  for (const r of results) {
    const slug = r.job_board_url.replace('https://join.com/companies/', '').replace('/jobs', '');
    r.company_name = slugToName(slug.replace(/-\d+$/, ''));
  }
  return results;
}

// ── Welcome to the Jungle (WTTJ) ──────────────────────────────────────────────
// Leading French job board, popular across EU (France, Germany, Spain, Belgium).
// Pattern: welcometothejungle.com/{locale}/companies/{slug}/jobs

const WTTJ_RESERVED = new Set(['www', 'api', 'app', 'blog', 'help', 'support', 'about', 'press', 'login', 'signup', 'en', 'fr', 'de', 'es', 'pt', 'companies', 'jobs']);

function extractWttjSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!['welcometothejungle.com', 'www.welcometothejungle.com'].includes(u.hostname)) return null;
    const parts = u.pathname.split('/').filter(Boolean);
    const companyIdx = parts.indexOf('companies');
    if (companyIdx === -1) return null;
    const seg = parts[companyIdx + 1];
    if (!seg || WTTJ_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    if (/^\d+$/.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromWelcomeToTheJungle(): Promise<CompanyDiscovery[]> {
  log.info('wttj: CDX discovery — Welcome to the Jungle (leading French/EU job board)');
  const [enSlugs, frSlugs] = await Promise.all([
    cdxEnumerateSlugs('welcometothejungle.com/en/companies/*/*', extractWttjSlug, 8000),
    cdxEnumerateSlugs('welcometothejungle.com/fr/companies/*/*', extractWttjSlug, 5000),
  ]);
  const merged = new Map(enSlugs);
  for (const [k, v] of frSlugs) merged.set(k, (merged.get(k) ?? 0) + v);
  log.info(`wttj: ${merged.size} unique company slugs`);
  if (!merged.size) return [];
  const results = slugsToDiscoveries(merged, s => `https://www.welcometothejungle.com/en/companies/${s}/jobs`, 'wttj');
  for (const r of results) {
    const slug = r.job_board_url.replace('https://www.welcometothejungle.com/en/companies/', '').replace('/jobs', '');
    r.company_name = slugToName(slug.replace(/-\d+$/, ''));
  }
  return results;
}

// ── TalentLyft ────────────────────────────────────────────────────────────────
// Growing EU ATS (Croatia-origin, popular across Central/Eastern Europe and DACH).
// Pattern: {company}.talentlyft.com/jobs

const TALENTLYFT_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'about']);

function extractTalentLyftSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.talentlyft.com')) return null;
    const s = u.hostname.replace('.talentlyft.com', '').toLowerCase();
    return (!s || TALENTLYFT_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromTalentLyft(): Promise<CompanyDiscovery[]> {
  log.info('talentlyft: CDX discovery — EU ATS (Croatia-origin, Central/Eastern Europe, DACH)');
  // *.talentlyft.com/jobs* has 17 CDX pages — use 6 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.talentlyft.com/jobs*', extractTalentLyftSlug, 4000, 6);
  log.info(`talentlyft: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.talentlyft.com/jobs`, 'talentlyft');
}

// ── Occupop ────────────────────────────────────────────────────────────────────
// Irish/UK ATS used by enterprises and public sector in Ireland, UK, and EU.
// Pattern: {company}.occupop.com/jobs

const OCCUPOP_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'about', 'pricing']);

function extractOccupopSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.occupop.com')) return null;
    const s = u.hostname.replace('.occupop.com', '').toLowerCase();
    return (!s || OCCUPOP_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromOccupop(): Promise<CompanyDiscovery[]> {
  log.info('occupop: CDX discovery — Irish/UK enterprise ATS (Ireland, UK, EU)');
  const slugs = await cdxEnumerateSlugs('*.occupop.com/jobs*', extractOccupopSlug, 3000);
  log.info(`occupop: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.occupop.com/jobs`, 'occupop');
}

// ── EasyCruit ─────────────────────────────────────────────────────────────────
// Scandinavian ATS dominant in Norway, Sweden, and Denmark.
// Used heavily by Nordic public sector (municipalities, universities) and enterprises.
// Pattern: {company}.easycruit.com/vacancy/{id}

const EASYCRUIT_RESERVED = new Set(['www', 'app', 'api', 'admin', 'support', 'help', 'blog', 'demo', 'test', 'staging', 'dev', 'static', 'assets', 'mail']);

function extractEasyCruitSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.easycruit.com')) return null;
    const s = u.hostname.replace('.easycruit.com', '').toLowerCase();
    return (!s || EASYCRUIT_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

// ── Varbi ─────────────────────────────────────────────────────────────────────
// Swedish/Scandinavian ATS used by municipalities, counties, universities, and enterprises.
// Popular across Sweden, Norway, Denmark — especially public sector.
// Pattern: {company}.varbi.com/{locale}/what:job/jobID:{id}/

const VARBI_RESERVED = new Set(['www', 'app', 'api', 'www2', 'static', 'assets', 'blog', 'support', 'help', 'admin', 'demo', 'test', 'staging', 'dev', 'mail', 'smtp']);

function extractVarbiSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.varbi.com')) return null;
    const s = u.hostname.replace('.varbi.com', '').toLowerCase();
    return (!s || VARBI_RESERVED.has(s) || s.length < 2) ? null : s;
  } catch { return null; }
}

export async function discoverFromVarbi(): Promise<CompanyDiscovery[]> {
  log.info('varbi: CDX discovery — Swedish/Scandinavian ATS (municipalities, universities, public sector)');
  // *.varbi.com/* has 157 CDX pages — use 10 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.varbi.com/*', extractVarbiSlug, 5000, 10);
  log.info(`varbi: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.varbi.com`, 'varbi');
}

export async function discoverFromEasyCruit(): Promise<CompanyDiscovery[]> {
  log.info('easycruit: CDX discovery — Scandinavian ATS (Norway, Sweden, Denmark public sector + enterprises)');
  // *.easycruit.com/vacancy/* has 64 CDX pages — use 10 pages for better coverage
  const slugs = await cdxEnumerateSlugs('*.easycruit.com/vacancy/*', extractEasyCruitSlug, 5000, 10);
  log.info(`easycruit: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.easycruit.com`, 'easycruit');
}
