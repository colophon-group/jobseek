/**
 * Workable company discovery via Wayback Machine CDX API.
 * Enumerates apply.workable.com/{slug} paths to find company slugs.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractWorkableSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (u.hostname !== 'apply.workable.com') return null;
    const slug = u.pathname.split('/').filter(Boolean)[0];
    if (!slug || slug.startsWith('api') || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromWorkable(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('workable: discovering company slugs via Wayback CDX');

  const slugCounts = await cdxEnumerateSlugs(
    'apply.workable.com/*',
    extractWorkableSlug,
    5000,
  );

  log.info(`workable/cdx: found ${slugCounts.size} unique company slugs`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://apply.workable.com/${slug}`,
    'workable',
  );
  log.info(`workable: ${results.length} companies discovered`);
  return results;
}
