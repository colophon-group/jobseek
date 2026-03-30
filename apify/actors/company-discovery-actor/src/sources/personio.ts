/**
 * Personio company discovery via Wayback Machine CDX API.
 * Personio is dominant in the German-speaking DACH region and popular EU-wide.
 * Companies host job boards at {slug}.jobs.personio.de (or .com).
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

function extractPersonioSlug(originalUrl: string): string | null {
  try {
    const u = new URL(originalUrl);
    const hostname = u.hostname;
    if (hostname.endsWith('.jobs.personio.de') || hostname.endsWith('.jobs.personio.com')) {
      const slug = hostname.split('.')[0];
      return slug && slug.length >= 2 ? slug.toLowerCase() : null;
    }
    return null;
  } catch {
    return null;
  }
}

export async function discoverFromPersonio(): Promise<CompanyDiscovery[]> {
  log.info('personio: discovering company subdomains via Wayback CDX');

  // Search both .de and .com TLDs
  const [deSlugs, comSlugs] = await Promise.all([
    cdxEnumerateSlugs('*.jobs.personio.de/*', extractPersonioSlug, 5000),
    cdxEnumerateSlugs('*.jobs.personio.com/*', extractPersonioSlug, 3000),
  ]);

  // Merge counts
  const merged = new Map<string, number>(deSlugs);
  for (const [slug, count] of comSlugs) {
    merged.set(slug, (merged.get(slug) ?? 0) + count);
  }

  log.info(`personio/cdx: found ${merged.size} unique Personio company subdomains`);
  if (merged.size === 0) return [];

  const results = slugsToDiscoveries(
    merged,
    slug => `https://${slug}.jobs.personio.de`,
    'personio',
  );
  log.info(`personio: ${results.length} companies discovered`);
  return results;
}
