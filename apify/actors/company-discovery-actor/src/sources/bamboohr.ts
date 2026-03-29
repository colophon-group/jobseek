/**
 * BambooHR company discovery via Wayback Machine CDX API.
 *
 * BambooHR companies use subdomains: {slug}.bamboohr.com
 * No public company directory exists, but archive.org has crawled thousands of
 * BambooHR career pages. We use CDX wildcard search (*.bamboohr.com/jobs) to
 * enumerate unique company slugs, then estimate job counts from slug appearance
 * frequency across snapshots.
 */
import { log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const CDX_API = 'http://web.archive.org/cdx/search/cdx';

async function listBambooHRSlugs(): Promise<Map<string, number>> {
  // Wildcard CDX query: all bamboohr.com subdomains' /jobs or /careers paths
  const url = `${CDX_API}?url=*.bamboohr.com/jobs*&output=json&fl=original&filter=statuscode:200&collapse=urlkey&limit=5000`;

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await gotScraping({ url, timeout: { request: 60_000 } });
      if (resp.statusCode !== 200) continue;

      const rows: string[][] = JSON.parse(resp.body);
      const slugCounts = new Map<string, number>();

      for (const [originalUrl] of rows.slice(1)) {
        try {
          const parsed = new URL(originalUrl);
          const hostname = parsed.hostname; // e.g. "stripe.bamboohr.com"
          if (!hostname.endsWith('.bamboohr.com')) continue;
          const slug = hostname.replace('.bamboohr.com', '');
          if (!slug || slug === 'www' || slug === 'api' || slug.length < 2) continue;
          slugCounts.set(slug, (slugCounts.get(slug) ?? 0) + 1);
        } catch {
          // skip malformed URLs
        }
      }

      return slugCounts;
    } catch (err) {
      log.warning(`bamboohr/cdx: attempt ${attempt + 1} failed: ${err}`);
      if (attempt < 2) await sleep(3_000 * (attempt + 1));
    }
  }
  return new Map();
}

/**
 * Discover companies from BambooHR via Wayback Machine CDX wildcard enumeration.
 * Returns one CompanyDiscovery per unique BambooHR subdomain found.
 */
export async function discoverFromBambooHR(): Promise<CompanyDiscovery[]> {
  log.info('bamboohr: discovering company subdomains via Wayback CDX wildcard');

  const slugCounts = await listBambooHRSlugs();
  log.info(`bamboohr/cdx: found ${slugCounts.size} unique BambooHR company subdomains`);

  if (slugCounts.size === 0) {
    log.warning('bamboohr/cdx: no subdomains found — source skipped');
    return [];
  }

  const now = new Date().toISOString();
  const results: CompanyDiscovery[] = [];

  for (const [slug, snapshotCount] of slugCounts) {
    // Convert slug to human-readable company name (e.g. "wpengine" → "WP Engine" isn't trivial,
    // so we use the slug as-is capitalised and let the downstream dedup handle it)
    const name = slug
      .replace(/-/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase());

    results.push({
      company_name: name,
      job_board_url: `https://${slug}.bamboohr.com/jobs`,
      estimated_jobs: Math.max(1, Math.round(snapshotCount / 3)), // rough estimate from crawl frequency
      source: 'bamboohr',
      discovered_at: now,
    });
  }

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  log.info(`bamboohr: ${results.length} companies discovered`);
  return results;
}
