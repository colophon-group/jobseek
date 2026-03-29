/**
 * Recruitee company discovery via Wayback Machine CDX API.
 * Enumerates *.recruitee.com subdomains to find company slugs.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractRecruiteeSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (!u.hostname.endsWith('.recruitee.com')) return null;
    const slug = u.hostname.replace('.recruitee.com', '');
    if (!slug || slug === 'www' || slug === 'app' || slug === 'api' || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromRecruitee(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('recruitee: discovering company subdomains via Wayback CDX wildcard');

  const slugCounts = await cdxEnumerateSlugs(
    '*.recruitee.com/*',
    extractRecruiteeSlug,
    5000,
  );

  log.info(`recruitee/cdx: found ${slugCounts.size} unique Recruitee company subdomains`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://${slug}.recruitee.com`,
    'recruitee',
  );
  log.info(`recruitee: ${results.length} companies discovered`);
  return results;
}
