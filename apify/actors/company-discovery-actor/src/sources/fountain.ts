/**
 * Fountain ATS company discovery via Wayback Machine CDX API.
 * Fountain is used by gig economy / shift-work companies: Uber (logistics), DoorDash,
 * Instacart, GoPuff, Lime, Bird, Deliveroo, etc.
 * Job boards are hosted at jobs.fountain.com/{company_slug}.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

const FOUNTAIN_RESERVED = new Set([
  'www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog',
  'admin', 'careers', 'demo', 'about', 'terms', 'privacy', 'apply',
]);

function extractFountainSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'jobs.fountain.com') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || FOUNTAIN_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    // Skip UUID-style segments (individual job applications, not company boards)
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromFountain(): Promise<CompanyDiscovery[]> {
  log.info('fountain: CDX discovery — gig economy ATS (Uber, DoorDash, Instacart, etc.)');

  // jobs.fountain.com/*/* has 248 CDX pages — use 10 pages for significantly better coverage
  const slugs = await cdxEnumerateSlugs(
    'jobs.fountain.com/*/*',
    extractFountainSlug,
    5000, 10,
  );

  log.info(`fountain: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];

  return slugsToDiscoveries(
    slugs,
    s => `https://jobs.fountain.com/${s}`,
    'fountain',
  );
}
