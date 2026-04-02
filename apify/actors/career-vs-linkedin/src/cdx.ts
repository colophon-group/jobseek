import { log } from 'apify';
import type { CdxSnapshot } from './types.js';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

interface CdxOptions {
  url: string;
  startDate?: string;   // YYYY-MM-DD
  endDate?: string;     // YYYY-MM-DD
  maxSnapshots?: number;
  /** If true, uses matchType=prefix to enumerate all archived sub-URLs. */
  prefix?: boolean;
  /** Additional CDX filter strings (e.g. "statuscode:200"). */
  filters?: string[];
  /** collapse parameter (e.g. "timestamp:8" = one per day). */
  collapse?: string;
}

/**
 * Query the Wayback CDX Search API.
 * Falls back to http:// and trailing-slash variants if the primary URL has no results.
 */
export async function fetchCdxSnapshots(opts: CdxOptions): Promise<CdxSnapshot[]> {
  const { url, startDate, endDate, maxSnapshots = 100, prefix = false, filters = ['statuscode:200'], collapse = 'timestamp:8' } = opts;

  const urlVariants: string[] = [url];
  try {
    const u = new URL(url);
    const altProtocol = u.protocol === 'https:' ? 'http' : 'https';
    urlVariants.push(url.replace(u.protocol, `${altProtocol}:`));
    if (u.pathname.endsWith('/') && u.pathname.length > 1) {
      urlVariants.push(url.replace(/\/$/, ''));
    } else {
      urlVariants.push(url + '/');
    }
  } catch { /* malformed URL */ }

  for (const variant of urlVariants) {
    const snapshots = await queryCdx(variant, { startDate, endDate, maxSnapshots, prefix, filters, collapse });
    if (snapshots.length > 0) {
      if (variant !== url) log.info(`CDX found results using alternative URL: ${variant}`);
      return snapshots;
    }
  }
  return [];
}

/**
 * CDX prefix enumeration — returns all unique archived URLs under a path prefix.
 * Used to discover individual LinkedIn job pages archived under a company's URL space.
 */
export async function fetchCdxUrlInventory(
  urlPrefix: string,
  startDate?: string,
  endDate?: string,
  limit = 500,
): Promise<string[]> {
  const params = new URLSearchParams({
    url: urlPrefix,
    matchType: 'prefix',
    output: 'json',
    fl: 'original',
    filter: 'statuscode:200',
    collapse: 'urlkey',
    limit: String(limit),
  });
  if (startDate) params.set('from', startDate.replace(/-/g, ''));
  if (endDate)   params.set('to',   endDate.replace(/-/g, ''));

  const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
  log.info('CDX URL inventory', { urlPrefix, limit });

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(apiUrl, { signal: AbortSignal.timeout(40_000), headers: { Accept: 'application/json' } });
      if (res.status === 429) { await sleep(20_000); continue; }
      if (!res.ok) { await sleep(5_000 * (attempt + 1)); continue; }
      const rows: string[][] = await res.json();
      if (!Array.isArray(rows) || rows.length < 2) return [];
      return rows.slice(1).map(r => r[0]).filter(Boolean);
    } catch (err) {
      log.warning(`CDX inventory error (attempt ${attempt + 1}): ${err}`);
      await sleep(5_000 * (attempt + 1));
    }
  }
  return [];
}

/**
 * Get the best (most recent) snapshot timestamp for a specific URL.
 */
export async function getLatestSnapshotTimestamp(url: string, beforeDate?: string): Promise<string | null> {
  const params = new URLSearchParams({
    url,
    output: 'json',
    fl: 'timestamp',
    filter: 'statuscode:200',
    limit: '1',
  });
  if (beforeDate) params.set('to', beforeDate.replace(/-/g, ''));
  const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
  try {
    const res = await fetch(apiUrl, { signal: AbortSignal.timeout(15_000) });
    if (!res.ok) return null;
    const rows: string[][] = await res.json();
    if (!Array.isArray(rows) || rows.length < 2) return null;
    return rows[1][0] ?? null;
  } catch { return null; }
}

async function queryCdx(
  url: string,
  opts: { startDate?: string; endDate?: string; maxSnapshots: number; prefix: boolean; filters: string[]; collapse: string },
): Promise<CdxSnapshot[]> {
  const params = new URLSearchParams({
    url,
    output: 'json',
    fl: 'timestamp,original,statuscode',
    limit: String(opts.maxSnapshots),
    collapse: opts.collapse,
  });
  if (opts.prefix) params.set('matchType', 'prefix');
  for (const f of opts.filters) params.append('filter', f);
  if (opts.startDate) params.set('from', opts.startDate.replace(/-/g, ''));
  if (opts.endDate)   params.set('to',   opts.endDate.replace(/-/g, ''));

  const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
  log.info('Querying Wayback CDX', { url, maxSnapshots: opts.maxSnapshots });

  // Prefix searches can be slow when the index is large; use a longer timeout
  const timeoutMs = opts.prefix ? 45_000 : 20_000;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(apiUrl, { signal: AbortSignal.timeout(timeoutMs), headers: { Accept: 'application/json' } });
      if (res.status === 429) { log.warning('CDX rate limited, waiting 20s'); await sleep(20_000); continue; }
      if (!res.ok) { await sleep(4_000 * (attempt + 1)); continue; }
      const rows: string[][] = await res.json();
      if (!Array.isArray(rows) || rows.length < 2) return [];
      const snapshots: CdxSnapshot[] = rows.slice(1).map(([timestamp, original]) => ({ timestamp, original }));
      log.info(`CDX returned ${snapshots.length} snapshots for ${url}`);
      return snapshots;
    } catch (err) {
      log.warning(`CDX query error (attempt ${attempt + 1}): ${err}`);
      await sleep(5_000 * (attempt + 1));
    }
  }
  return [];
}
