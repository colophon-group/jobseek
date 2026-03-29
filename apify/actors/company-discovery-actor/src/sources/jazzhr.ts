/**
 * JazzHR company discovery via Wayback Machine CDX API.
 * JazzHR companies host job boards at {slug}.applytojob.com.
 * Very popular with SMBs in the US.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractJazzHRSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (!u.hostname.endsWith('.applytojob.com')) return null;
    const slug = u.hostname.replace('.applytojob.com', '');
    if (!slug || slug === 'www' || slug === 'app' || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromJazzHR(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('jazzhr: discovering company subdomains via Wayback CDX');

  const slugCounts = await cdxEnumerateSlugs(
    '*.applytojob.com/*',
    extractJazzHRSlug,
    5000,
  );

  log.info(`jazzhr/cdx: found ${slugCounts.size} unique JazzHR company subdomains`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://${slug}.applytojob.com/apply`,
    'jazzhr',
  );
  log.info(`jazzhr: ${results.length} companies discovered`);
  return results;
}
