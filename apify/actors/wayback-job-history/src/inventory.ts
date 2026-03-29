import { log } from 'apify';
import type { CdxSnapshot, JobRecord } from './types.js';
import { scoreGhost } from './ghost.js';

/**
 * Fetch ALL archived URLs under a portal via CDX prefix/domain search.
 * One record per unique URL (collapse=urlkey), with earliest timestamp.
 * Then a second pass gets the LATEST timestamp per URL, so we can compute duration.
 */
export async function fetchUrlInventory(
  baseUrl: string,
  startDate?: string,
  endDate?: string,
  limit = 5000,
): Promise<CdxSnapshot[]> {
  const url = new URL(baseUrl);
  // Use domain/* prefix to catch all paths including Workday subpaths
  const searchUrl = `${url.protocol}//${url.hostname}${url.pathname}`;

  const params = new URLSearchParams({
    url: searchUrl + (searchUrl.endsWith('/') ? '*' : '/*'),
    matchType: 'prefix',
    output: 'json',
    fl: 'timestamp,original,statuscode',
    filter: 'statuscode:200',
    collapse: 'urlkey',
    limit: String(limit),
  });

  if (startDate) params.set('from', startDate.replace(/-/g, ''));
  if (endDate)   params.set('to',   endDate.replace(/-/g, ''));

  const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
  log.info('CDX inventory search', { apiUrl });

  const rows = await cdxFetch(apiUrl);
  log.info(`CDX inventory: ${rows.length} unique URLs found`);
  return rows;
}

/**
 * For a specific URL, fetch first and last archived timestamps.
 */
export async function fetchUrlLifespan(
  originalUrl: string,
  startDate?: string,
  endDate?: string,
): Promise<{ first: string; last: string; count: number } | null> {
  const params = new URLSearchParams({
    url: originalUrl,
    output: 'json',
    fl: 'timestamp',
    filter: 'statuscode:200',
    limit: '1000',
  });

  if (startDate) params.set('from', startDate.replace(/-/g, ''));
  if (endDate)   params.set('to',   endDate.replace(/-/g, ''));

  const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
  const rows = await cdxFetch(apiUrl);
  if (rows.length === 0) return null;

  const timestamps = rows.map(r => r.timestamp).sort();
  return {
    first: timestamps[0],
    last: timestamps[timestamps.length - 1],
    count: timestamps.length,
  };
}

/**
 * Filter a URL list to only those that look like individual job postings
 * (not category pages, search pages, home pages, etc.)
 */
export function filterJobUrls(snapshots: CdxSnapshot[]): CdxSnapshot[] {
  return snapshots.filter(s => {
    const u = s.original.toLowerCase();

    // Skip asset/API/redirect URLs
    if (/\.(ico|png|jpg|gif|svg|css|js|json|xml|txt|woff|woff2|ttf)(\?|$)/.test(u)) return false;
    if (u.includes('/api/') || u.includes('/cdn-cgi/')) return false;

    // Must match job-like URL patterns
    return (
      /\/job\//.test(u) ||
      /\/jobs\/\d/.test(u) ||
      /[?&](jid|job_id|jobId|req_id|reqId|jdid|JobId)=/i.test(u) ||
      /\/j\/[a-z0-9-]{8,}/.test(u) ||       // Lever: /j/{uuid}
      /\/posting\/[a-z0-9-]{8,}/i.test(u) || // Ashby
      /\/vacancy\//i.test(u) ||
      /\/position\/\w/i.test(u) ||
      // Workday: /job/{location}/{title}_{id}
      /\/job\/[^/]+\/[^/]+-[A-Z0-9]+$/.test(u)
    );
  });
}

/**
 * Extract a human-readable job title from a URL slug.
 * Works for Workday, Lever, and similar slug-based URLs.
 */
export function titleFromUrl(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.pathname.split('/').filter(Boolean);

    // Workday: /AccentureCareers/job/New-York/Data-Analytics-Manager_R00132456
    const lastPart = parts[parts.length - 1] ?? '';
    const beforeUnderscore = lastPart.split('_')[0];
    if (beforeUnderscore && /[A-Z]/.test(beforeUnderscore)) {
      return beforeUnderscore.replace(/-/g, ' ');
    }

    // Lever: /stripe/abc123-senior-software-engineer
    if (u.hostname === 'jobs.lever.co' && parts.length >= 2) {
      const slug = parts[parts.length - 1];
      // Remove UUID prefix: abc123-senior-... → senior-...
      const noUuid = slug.replace(/^[0-9a-f-]{36}-/, '').replace(/^[0-9a-f]{8}-[0-9a-f]{4}-.*?-/, '');
      return noUuid.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // Generic: take the most descriptive path segment
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      if (p.length > 5 && !/^\d+$/.test(p) && !/^[a-f0-9-]{36}$/.test(p)) {
        return p.replace(/-/g, ' ').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      }
    }
  } catch {
    // ignore malformed URLs
  }
  return '';
}

/**
 * Build JobRecord list from CDX inventory snapshots + lifespan data.
 * Batches lifespan lookups to avoid rate limits.
 */
export async function buildJobRecords(
  jobSnapshots: CdxSnapshot[],
  startDate?: string,
  endDate?: string,
  delayMs = 800,
): Promise<JobRecord[]> {
  const records: JobRecord[] = [];

  for (let i = 0; i < jobSnapshots.length; i++) {
    const snap = jobSnapshots[i];
    log.debug(`Lifespan [${i + 1}/${jobSnapshots.length}]: ${snap.original}`);

    const lifespan = await fetchUrlLifespan(snap.original, startDate, endDate);
    if (!lifespan) {
      await sleep(delayMs / 2);
      continue;
    }

    const firstDate = cdxDateToIso(lifespan.first);
    const lastDate  = cdxDateToIso(lifespan.last);
    const duration  = daysBetween(firstDate, lastDate);

    // Title from URL slug; will be enriched with real title if we fetch the page
    const title = titleFromUrl(snap.original) || snap.original;

    const { score, reason } = scoreGhost(duration, lifespan.count, false);

    records.push({
      title,
      url: snap.original,
      firstSeen: firstDate,
      lastSeen: lastDate,
      durationDays: duration,
      archiveCount: lifespan.count,
      reposted: false,
      ghostScore: score,
      ghostReason: reason,
    });

    await sleep(delayMs);
  }

  return records;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function cdxFetch(apiUrl: string): Promise<CdxSnapshot[]> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(apiUrl, {
        signal: AbortSignal.timeout(60_000),
        headers: { 'Accept': 'application/json' },
      });

      if (res.status === 429) {
        await sleep(20_000);
        continue;
      }
      if (!res.ok) {
        await sleep(5_000 * (attempt + 1));
        continue;
      }

      const rows: string[][] = await res.json();
      if (!Array.isArray(rows) || rows.length < 2) return [];
      return rows.slice(1).map(([timestamp, original]) => ({ timestamp, original }));
    } catch (err) {
      log.warning(`CDX fetch error (attempt ${attempt + 1}): ${err}`);
      await sleep(5_000 * (attempt + 1));
    }
  }
  return [];
}

export function cdxDateToIso(timestamp: string): string {
  const y = timestamp.slice(0, 4);
  const m = timestamp.slice(4, 6);
  const d = timestamp.slice(6, 8);
  return `${y}-${m}-${d}`;
}

export function daysBetween(a: string, b: string): number {
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 86_400_000);
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms));
}
