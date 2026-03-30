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
    if (u.hostname !== 'boards.greenhouse.io' && u.hostname !== 'job-boards.greenhouse.io') return null;
    const token = u.pathname.split('/').filter(Boolean)[0];
    if (!token || ['embed', 'js', 'css', 'api', 'assets'].includes(token) || token.length < 2) return null;
    return token.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromGreenhouseCdx(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('greenhouse-cdx: discovering board tokens via Wayback CDX');

  // Scan both Greenhouse URL formats in parallel
  const [boards, jobBoards] = await Promise.all([
    cdxEnumerateSlugs('boards.greenhouse.io/*', extractGreenhouseToken, 10_000),
    cdxEnumerateSlugs('job-boards.greenhouse.io/*', extractGreenhouseToken, 5_000),
  ]);

  // Merge (prefer boards.greenhouse.io URL)
  const merged = new Map(boards);
  for (const [k, v] of jobBoards) merged.set(k, (merged.get(k) ?? 0) + v);

  log.info(`greenhouse-cdx: found ${merged.size} unique board tokens (boards + job-boards)`);
  if (merged.size === 0) return [];

  const results = slugsToDiscoveries(
    merged,
    token => `https://boards.greenhouse.io/${token}`,
    'greenhouse-cdx',
  );
  log.info(`greenhouse-cdx: ${results.length} companies discovered`);
  return results;
}
