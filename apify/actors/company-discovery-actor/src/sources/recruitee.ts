/**
 * Recruitee company discovery via Wayback Machine CDX API.
 *
 * Recruitee companies use subdomains: {slug}.recruitee.com
 * CDX wildcard search discovers unique company slugs from archived pages.
 */
import { log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const CDX_API = 'http://web.archive.org/cdx/search/cdx';

async function listRecruiteeSlugs(): Promise<Map<string, number>> {
  const url = `${CDX_API}?url=*.recruitee.com/*&output=json&fl=original&filter=statuscode:200&collapse=urlkey&limit=5000`;

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await gotScraping({ url, timeout: { request: 60_000 } });
      if (resp.statusCode !== 200) continue;

      const rows: string[][] = JSON.parse(resp.body);
      const slugCounts = new Map<string, number>();

      for (const [originalUrl] of rows.slice(1)) {
        try {
          const parsed = new URL(originalUrl);
          const hostname = parsed.hostname;
          if (!hostname.endsWith('.recruitee.com')) continue;
          const slug = hostname.replace('.recruitee.com', '');
          if (!slug || slug === 'www' || slug === 'app' || slug === 'api' || slug.length < 2) continue;
          slugCounts.set(slug, (slugCounts.get(slug) ?? 0) + 1);
        } catch {
          // skip malformed URLs
        }
      }

      return slugCounts;
    } catch (err) {
      log.warning(`recruitee/cdx: attempt ${attempt + 1} failed: ${err}`);
      if (attempt < 2) await sleep(3_000 * (attempt + 1));
    }
  }
  return new Map();
}

export async function discoverFromRecruitee(): Promise<CompanyDiscovery[]> {
  log.info('recruitee: discovering company subdomains via Wayback CDX wildcard');

  const slugCounts = await listRecruiteeSlugs();
  log.info(`recruitee/cdx: found ${slugCounts.size} unique Recruitee company subdomains`);

  if (slugCounts.size === 0) {
    log.warning('recruitee/cdx: no subdomains found — source skipped');
    return [];
  }

  const now = new Date().toISOString();
  const results: CompanyDiscovery[] = [];

  for (const [slug, snapshotCount] of slugCounts) {
    const name = slug
      .replace(/-/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase());

    results.push({
      company_name: name,
      job_board_url: `https://${slug}.recruitee.com`,
      estimated_jobs: Math.max(1, Math.round(snapshotCount / 3)),
      source: 'recruitee',
      discovered_at: now,
    });
  }

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  log.info(`recruitee: ${results.length} companies discovered`);
  return results;
}
