/**
 * Shared CDX-based company discovery helper for ATS platforms that use
 * either subdomain patterns (*.ats.com) or path patterns (ats.com/{slug}).
 */
import { log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const CDX_API = 'http://web.archive.org/cdx/search/cdx';

/**
 * Enumerate unique slugs from a CDX URL pattern.
 * Supports multi-page CDX pagination via showResumeKey — automatically follows
 * resume keys to fetch more pages until results are exhausted or maxPages is reached.
 */
export async function cdxEnumerateSlugs(
  cdxUrlPattern: string,
  extractSlug: (originalUrl: string) => string | null,
  limit = 5000,
  maxPages = 4,
): Promise<Map<string, number>> {
  const slugCounts = new Map<string, number>();
  let resumeKey: string | null = null;
  let page = 0;

  while (page < maxPages) {
    // Do NOT encode the cdxUrlPattern — CDX API requires literal * wildcards.
    const baseParams = `url=${cdxUrlPattern}&output=json&fl=original&filter=statuscode:200&collapse=urlkey&limit=${limit}&showResumeKey=true`;
    const url = resumeKey
      ? `${CDX_API}?${baseParams}&resumeKey=${encodeURIComponent(resumeKey)}`
      : `${CDX_API}?${baseParams}`;

    let rows: string[][] = [];
    let success = false;

    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const resp = await gotScraping({ url, timeout: { request: 60_000 } });
        if (resp.statusCode !== 200) {
          if (attempt < 2) await sleep(3_000 * (attempt + 1));
          continue;
        }
        rows = JSON.parse(resp.body) as string[][];
        success = true;
        break;
      } catch (err) {
        log.warning(`cdx-enumerate p${page}: attempt ${attempt + 1} failed: ${err}`);
        if (attempt < 2) await sleep(3_000 * (attempt + 1));
      }
    }

    if (!success || rows.length < 2) break;

    // Check if the last row is a resume key (single-element array)
    const lastRow = rows[rows.length - 1];
    const hasResumeKey = lastRow?.length === 1 && lastRow[0] !== 'original';
    if (hasResumeKey) {
      resumeKey = lastRow[0];
      rows = rows.slice(0, -1); // strip resume key row from data
    } else {
      resumeKey = null;
    }

    // CDX includes header row ["original"] on every page — filter it out
    const dataRows = rows.filter(row => row[0] !== 'original' && row[0]);
    for (const row of dataRows) {
      const originalUrl = row[0];
      if (!originalUrl) continue;
      const slug = extractSlug(originalUrl);
      if (slug) slugCounts.set(slug, (slugCounts.get(slug) ?? 0) + 1);
    }

    log.debug(`cdx-enumerate: p${page} → ${dataRows.length} rows (total slugs: ${slugCounts.size})`);

    if (!resumeKey) break; // no more pages
    page++;
    await sleep(1_500); // polite pause between CDX pages
  }

  return slugCounts;
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
