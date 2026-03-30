import { log } from 'apify';
import type { CdxSnapshot } from './types.js';

interface CdxOptions {
  url: string;
  startDate?: string;   // YYYY-MM-DD
  endDate?: string;     // YYYY-MM-DD
  maxSnapshots?: number;
}

/**
 * Query the Wayback CDX Search API for all archived snapshots of a URL.
 * Uses collapse=timestamp:8 to return at most one snapshot per day.
 * If the primary URL returns 0 results, tries alternative URL variants.
 */
export async function fetchCdxSnapshots(opts: CdxOptions): Promise<CdxSnapshot[]> {
  const { url, startDate, endDate, maxSnapshots = 365 } = opts;

  // Build alternative URL variants to try if primary returns nothing
  const urlVariants: string[] = [url];
  try {
    const u = new URL(url);
    // Try opposite protocol
    const altProtocol = u.protocol === 'https:' ? 'http' : 'https';
    urlVariants.push(url.replace(u.protocol, `${altProtocol}:`));
    // Try with/without trailing slash on pathname
    if (u.pathname.endsWith('/') && u.pathname.length > 1) {
      urlVariants.push(url.replace(/\/$/, ''));
    } else {
      urlVariants.push(url + '/');
    }
  } catch { /* malformed URL — just use as-is */ }

  for (const variant of urlVariants) {
    const snapshots = await queryCdx(variant, startDate, endDate, maxSnapshots);
    if (snapshots.length > 0) {
      if (variant !== url) log.info(`CDX found results using alternative URL: ${variant}`);
      return snapshots;
    }
  }

  return [];
}

async function queryCdx(url: string, startDate?: string, endDate?: string, maxSnapshots = 365): Promise<CdxSnapshot[]> {
  const params = new URLSearchParams({
    url,
    output: 'json',
    fl: 'timestamp,original,statuscode',
    filter: 'statuscode:200',
    collapse: 'timestamp:8',   // dedupe to one per calendar day
    limit: String(maxSnapshots),
  });

  if (startDate) params.set('from', startDate.replace(/-/g, ''));
  if (endDate)   params.set('to',   endDate.replace(/-/g, ''));

  const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
  log.info('Querying Wayback CDX API', { apiUrl });

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(apiUrl, {
        signal: AbortSignal.timeout(30_000),
        headers: { 'Accept': 'application/json' },
      });

      if (res.status === 429) {
        log.warning('CDX API rate limited, waiting 20s');
        await sleep(20_000);
        continue;
      }

      if (!res.ok) {
        log.warning(`CDX API returned HTTP ${res.status}`);
        await sleep(5_000 * (attempt + 1));
        continue;
      }

      const rows: string[][] = await res.json();
      if (!Array.isArray(rows) || rows.length < 2) {
        return [];
      }

      // Skip header row [timestamp, original, statuscode]
      const snapshots: CdxSnapshot[] = rows.slice(1).map(([timestamp, original]) => ({
        timestamp,
        original,
      }));

      log.info(`CDX returned ${snapshots.length} daily snapshots`);
      return snapshots;
    } catch (err) {
      log.warning(`CDX fetch error (attempt ${attempt + 1}): ${err}`);
      await sleep(5_000 * (attempt + 1));
    }
  }

  return [];
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms));
}
