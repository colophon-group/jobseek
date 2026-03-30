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

function extractBreezySlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('.breezy.hr')) return null;
    const s = u.hostname.replace('.breezy.hr', '');
    return (!s || s === 'www' || s === 'app' || s === 'api' || s.length < 2) ? null : s.toLowerCase();
  } catch { return null; }
}

export async function discoverFromBreezyHR(): Promise<CompanyDiscovery[]> {
  log.info('breezyhr: CDX wildcard discovery');
  const slugs = await cdxEnumerateSlugs('*.breezy.hr/*', extractBreezySlug, 5000);
  log.info(`breezyhr: ${slugs.size} unique subdomains`);
  return slugs.size ? slugsToDiscoveries(slugs, s => `https://${s}.breezy.hr`, 'breezyhr') : [];
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
  const slugs = await cdxEnumerateSlugs('*.icims.com/jobs*', extractICIMSSlug, 10000);
  log.info(`icims: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];
  return slugsToDiscoveries(slugs, s => `https://${s}.icims.com/jobs/search`, 'icims');
}
