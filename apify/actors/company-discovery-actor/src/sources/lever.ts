/**
 * Lever company discovery via Wayback Machine CDX API.
 * Enumerates jobs.lever.co/{slug} paths to find company slugs.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractLeverSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (u.hostname !== 'jobs.lever.co') return null;
    const slug = u.pathname.split('/').filter(Boolean)[0];
    if (!slug || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromLever(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('lever: discovering company slugs via Wayback CDX');

  const slugCounts = await cdxEnumerateSlugs(
    'jobs.lever.co/*',
    extractLeverSlug,
    8000,  // Lever has a lot of companies
  );

  log.info(`lever/cdx: found ${slugCounts.size} unique company slugs`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://jobs.lever.co/${slug}`,
    'lever',
  );
  log.info(`lever: ${results.length} companies discovered`);
  return results;
}
