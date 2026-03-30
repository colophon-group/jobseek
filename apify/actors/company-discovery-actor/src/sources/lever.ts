/**
 * Lever company discovery via Wayback Machine CDX API.
 * Enumerates jobs.lever.co/{slug} paths to find company slugs.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractLeverSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (u.hostname !== 'jobs.lever.co' && u.hostname !== 'jobs.eu.lever.co') return null;
    const slug = u.pathname.split('/').filter(Boolean)[0];
    if (!slug || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromLever(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('lever: discovering company slugs via Wayback CDX (US + EU)');

  // jobs.lever.co/* has 367 CDX pages (~36.7M entries) — use 12 pages for broader coverage
  const [usSlugs, euSlugs] = await Promise.all([
    cdxEnumerateSlugs('jobs.lever.co/*', extractLeverSlug, 10000, 12),
    // jobs.eu.lever.co/* has 6 CDX pages — use 6 pages for full coverage
  cdxEnumerateSlugs('jobs.eu.lever.co/*', extractLeverSlug, 5000, 6),
  ]);

  const merged = new Map(usSlugs);
  for (const [k, v] of euSlugs) merged.set(k, (merged.get(k) ?? 0) + v);

  log.info(`lever/cdx: found ${merged.size} unique company slugs (US + EU)`);
  if (merged.size === 0) return [];

  const results = slugsToDiscoveries(
    merged,
    slug => `https://jobs.lever.co/${slug}`,
    'lever',
  );
  log.info(`lever: ${results.length} companies discovered`);
  return results;
}
