/**
 * Breezy HR company discovery via Wayback Machine CDX API.
 * Breezy HR companies host job boards at {slug}.breezy.hr.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractBreezySlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (!u.hostname.endsWith('.breezy.hr')) return null;
    const slug = u.hostname.replace('.breezy.hr', '');
    if (!slug || slug === 'www' || slug === 'app' || slug === 'api' || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromBreezyHR(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('breezyhr: discovering company subdomains via Wayback CDX');

  const slugCounts = await cdxEnumerateSlugs(
    '*.breezy.hr/*',
    extractBreezySlug,
    5000,
  );

  log.info(`breezyhr/cdx: found ${slugCounts.size} unique Breezy HR company subdomains`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://${slug}.breezy.hr`,
    'breezyhr',
  );
  log.info(`breezyhr: ${results.length} companies discovered`);
  return results;
}
