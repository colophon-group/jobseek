/**
 * Teamtailor company discovery via Wayback Machine CDX API.
 * Teamtailor is the dominant ATS in Scandinavia and popular across Europe.
 * Companies host job boards at {slug}.teamtailor.com.
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';

function extractTeamtailorSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    if (!u.hostname.endsWith('.teamtailor.com')) return null;
    const slug = u.hostname.replace('.teamtailor.com', '');
    if (!slug || slug === 'www' || slug === 'app' || slug === 'api' || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch {
    return null;
  }
}

export async function discoverFromTeamtailor(): Promise<import('../types.js').CompanyDiscovery[]> {
  log.info('teamtailor: discovering company subdomains via Wayback CDX');

  // *.teamtailor.com/* has 166 CDX pages (~16.6M entries) — use 10 pages for better coverage
  const slugCounts = await cdxEnumerateSlugs(
    '*.teamtailor.com/*',
    extractTeamtailorSlug,
    10000, 10,
  );

  log.info(`teamtailor/cdx: found ${slugCounts.size} unique Teamtailor company subdomains`);
  if (slugCounts.size === 0) return [];

  const results = slugsToDiscoveries(
    slugCounts,
    slug => `https://${slug}.teamtailor.com/jobs`,
    'teamtailor',
  );
  log.info(`teamtailor: ${results.length} companies discovered`);
  return results;
}
