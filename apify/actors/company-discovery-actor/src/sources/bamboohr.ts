/**
 * BambooHR company discovery via Wayback Machine CDX API.
 *
 * BambooHR companies use subdomains: {slug}.bamboohr.com
 * CDX wildcard search discovers unique company slugs from archived pages.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractBambooSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (!u.hostname.endsWith('.bamboohr.com')) return null;
    const slug = u.hostname.replace('.bamboohr.com', '');
    if (!slug || slug === 'www' || slug === 'api' || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromBambooHR(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('bamboohr: discovering company subdomains via Wayback CDX wildcard');

  const slugCounts = await cdxEnumerateSlugs(
    '*.bamboohr.com/jobs*',
    extractBambooSlug,
    5000,
  );

  log.info(`bamboohr/cdx: found ${slugCounts.size} unique BambooHR company subdomains`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://${slug}.bamboohr.com/jobs`,
    'bamboohr',
  );
  log.info(`bamboohr: ${results.length} companies discovered`);
  return results;
}
