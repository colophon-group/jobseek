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
  const [io, de] = await Promise.all([
    cdxEnumerateSlugs('*.softgarden.io/*', extractSoftgardenSlug, 5000),
    cdxEnumerateSlugs('*.softgarden.de/*', extractSoftgardenSlug, 2000),
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
  const slugs = await cdxEnumerateSlugs('join.com/companies/*/jobs', extractJoinSlug, 5000);
  log.info(`join.com: ${slugs.size} companies`);
  if (!slugs.size) return [];

  const results = slugsToDiscoveries(slugs, s => `https://join.com/companies/${s}/jobs`, 'join');
  for (const r of results) {
    const slug = r.job_board_url.replace('https://join.com/companies/', '').replace('/jobs', '');
    r.company_name = slugToName(slug.replace(/-\d+$/, ''));
  }
  return results;
}
