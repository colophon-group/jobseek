/**
 * Greenhouse CDX discovery — complements the hardcoded-token greenhouse source.
 * Discovers company board tokens from boards.greenhouse.io/* via Wayback CDX,
 * catching boards not in the curated 790-token list.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractGreenhouseToken(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (u.hostname !== 'boards.greenhouse.io') return null;
    const token = u.pathname.split('/').filter(Boolean)[0];
    if (!token || token === 'embed' || token === 'js' || token.length < 2) return null;
    return token.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromGreenhouseCdx(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('greenhouse-cdx: discovering board tokens via Wayback CDX');

  const slugCounts = await cdxEnumerateSlugs(
    'boards.greenhouse.io/*',
    extractGreenhouseToken,
    10_000, // Greenhouse has lots of boards
  );

  log.info(`greenhouse-cdx: found ${slugCounts.size} unique board tokens`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    token => `https://boards.greenhouse.io/${token}`,
    'greenhouse-cdx',
  );
  log.info(`greenhouse-cdx: ${results.length} companies discovered`);
  return results;
}
