/**
 * JazzHR + Taleo (Oracle) company discovery via Wayback CDX.
 *
 * JazzHR: {slug}.applytojob.com (SMB US ATS)
 * Taleo: {slug}.taleo.net (Oracle enterprise ATS, 5000+ companies)
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

// ── JazzHR ────────────────────────────────────────────────────────────────────

function extractJazzHRSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.applytojob.com')) return null;
    const s = u.hostname.replace('.applytojob.com', '');
    return (!s || s === 'www' || s === 'app' || s.length < 2) ? null : s.toLowerCase();
  } catch { return null; }
}

export async function discoverFromJazzHR(): Promise<CompanyDiscovery[]> {
  log.info('jazzhr: CDX wildcard discovery');
  const slugs = await cdxEnumerateSlugs('*.applytojob.com/*', extractJazzHRSlug, 5000);
  log.info(`jazzhr: ${slugs.size} unique subdomains`);
  return slugs.size ? slugsToDiscoveries(slugs, s => `https://${s}.applytojob.com/apply`, 'jazzhr') : [];
}

// ── Taleo (Oracle) ────────────────────────────────────────────────────────────

const TALEO_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'preview', 'cdn', 'mail', 'secure', 'talent']);

function extractTaleoSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.taleo.net')) return null;
    const s = u.hostname.replace('.taleo.net', '').toLowerCase();
    if (!s || TALEO_RESERVED.has(s) || s.length < 2) return null;
    return s;
  } catch { return null; }
}

export async function discoverFromTaleo(): Promise<CompanyDiscovery[]> {
  log.info('taleo: CDX wildcard discovery — Oracle enterprise ATS with 5000+ companies');
  const slugs = await cdxEnumerateSlugs('*.taleo.net/careersection*', extractTaleoSlug, 10000);
  log.info(`taleo: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.taleo.net/careersection/2/jobsearch.ftl`, 'taleo');
}
