/**
 * Shared CDX-based company discovery helper for ATS platforms that use
 * either subdomain patterns (*.ats.com) or path patterns (ats.com/{slug}).
 */
import { log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const CDX_API = 'http://web.archive.org/cdx/search/cdx';

/** Enumerate unique slugs from a CDX URL pattern. */
export async function cdxEnumerateSlugs(
  cdxUrlPattern: string,
  extractSlug: (originalUrl: string) => string | null,
  limit = 5000,
): Promise<Map<string, number>> {
  const url = `${CDX_API}?url=${encodeURIComponent(cdxUrlPattern)}&output=json&fl=original&filter=statuscode:200&collapse=urlkey&limit=${limit}`;

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await gotScraping({ url, timeout: { request: 60_000 } });
      if (resp.statusCode !== 200) continue;

      const rows: string[][] = JSON.parse(resp.body);
      const slugCounts = new Map<string, number>();

      for (const [originalUrl] of rows.slice(1)) {
        const slug = extractSlug(originalUrl);
        if (slug) slugCounts.set(slug, (slugCounts.get(slug) ?? 0) + 1);
      }

      return slugCounts;
    } catch (err) {
      log.warning(`cdx-enumerate: attempt ${attempt + 1} failed: ${err}`);
      if (attempt < 2) await sleep(3_000 * (attempt + 1));
    }
  }
  return new Map();
}

/** Convert a slug to a readable company name. */
export function slugToName(slug: string): string {
  return slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/** Build CompanyDiscovery records from a slug → count map. */
export function slugsToDiscoveries(
  slugCounts: Map<string, number>,
  buildUrl: (slug: string) => string,
  source: string,
): CompanyDiscovery[] {
  const now = new Date().toISOString();
  const results: CompanyDiscovery[] = [];

  for (const [slug, count] of slugCounts) {
    results.push({
      company_name: slugToName(slug),
      job_board_url: buildUrl(slug),
      estimated_jobs: Math.max(1, Math.round(count / 3)),
      source,
      discovered_at: now,
    });
  }

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  return results;
}
