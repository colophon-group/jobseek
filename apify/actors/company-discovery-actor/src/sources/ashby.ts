/**
 * Ashby company discovery via Wayback Machine CDX API.
 * Enumerates jobs.ashbyhq.com/{slug} paths to find company slugs.
 * Ashby is the dominant ATS for YC/VC-backed startups as of 2024.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractAshbySlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (u.hostname !== 'jobs.ashbyhq.com') return null;
    const slug = u.pathname.split('/').filter(Boolean)[0];
    if (!slug || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromAshby(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('ashby: discovering company slugs via Wayback CDX');

  const slugCounts = await cdxEnumerateSlugs(
    'jobs.ashbyhq.com/*',
    extractAshbySlug,
    5000,
  );

  log.info(`ashby/cdx: found ${slugCounts.size} unique company slugs`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://jobs.ashbyhq.com/${slug}`,
    'ashby',
  );
  log.info(`ashby: ${results.length} companies discovered`);
  return results;
}
