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
 * For a specific URL, fetch all archived timestamps to compute lifespan and detect reposts.
 * A repost is detected when there is a gap of >45 days between consecutive captures.
 */
export async function fetchUrlLifespan(
  originalUrl: string,
  startDate?: string,
  endDate?: string,
): Promise<{ first: string; last: string; count: number; reposted: boolean; repostCount: number } | null> {
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

  // Detect reposts: gap >45 days between consecutive CDX captures
  let repostCount = 0;
  let lastTs = timestamps[0];
  for (let i = 1; i < timestamps.length; i++) {
    const gapDays = daysBetween(cdxDateToIso(lastTs), cdxDateToIso(timestamps[i]));
    if (gapDays > 45) repostCount++;
    lastTs = timestamps[i];
  }

  return {
    first: timestamps[0],
    last: timestamps[timestamps.length - 1],
    count: timestamps.length,
    reposted: repostCount > 0,
    repostCount,
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
      /[?&](jid|job_id|jobId|req_id|reqId|jdid|JobId|career_job_req_id|jobReqId|job_number|openingId)=/i.test(u) ||
      /\/j\/[a-z0-9-]{8,}/.test(u) ||          // Lever: /j/{uuid}
      /\/posting\/[a-z0-9-]{8,}/i.test(u) ||   // Ashby
      /\/vacancy\//i.test(u) ||
      /\/vacature\//i.test(u) ||                // Dutch
      /\/position\/\w/i.test(u) ||
      /\/p\/[a-z0-9-]{10,}/.test(u) ||         // Breezy HR: /p/{long-slug}
      /\/offre\//.test(u) ||                    // French ATS
      /\/stellenangebot\//.test(u) ||           // German (Softgarden)
      /\/stellen\//.test(u) ||                  // German ATS
      /\/stelle\//.test(u) ||                   // German ATS
      /\/open-positions\//.test(u) ||
      /\/apply\/[a-z0-9]{6,}/i.test(u) ||      // JazzHR: {company}.applytojob.com/apply/{hash}
      /\/o\/[a-z0-9][a-z0-9_-]{5,}/i.test(u) || // Recruitee: {company}.recruitee.com/o/{position-slug}
      /\/aanbod\/[a-z0-9-]{4,}/i.test(u) ||    // Dutch ATS (aanbod = offer)
      /\/rol\/[a-z0-9-]{4,}/i.test(u) ||       // Spanish ATS (rol = role)
      /\/requisition\/\d/.test(u) ||            // SuccessFactors: /careers/requisition/{id}
      /\/joblistings\/\d/.test(u) ||            // SuccessFactors: /careers/joblistings/{id}
      /\/careersection\/.*\/jobdetail/i.test(u) || // Taleo: /careersection/.../jobdetail.ftl?job=...
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

    // Recruitee: {company}.recruitee.com/o/software-engineer-remote-12345
    // Strip trailing numeric ID segment: "software-engineer-remote-12345" → "Software Engineer Remote"
    if (u.hostname.endsWith('.recruitee.com') && parts[0] === 'o' && parts[1]) {
      const slug = parts[1].replace(/-\d{4,}$/, '').replace(/-\d{4,}-/, '-');
      return slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // SmartRecruiters: jobs.smartrecruiters.com/CompanyName/123456789-Job-Title
    if (u.hostname === 'jobs.smartrecruiters.com' && parts.length >= 2) {
      const slug = parts[parts.length - 1];
      // Strip leading numeric ID: "1234567890-senior-engineer" → "Senior Engineer"
      const noId = slug.replace(/^\d{8,}-/, '');
      if (noId !== slug) return noId.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // JazzHR: {company}.applytojob.com/apply/{hash} — title not in URL, skip
    if (u.hostname.endsWith('.applytojob.com')) return '';

    // SuccessFactors: {company}.successfactors.com/careers/joblistings/12345 → no title in URL
    if (u.hostname.endsWith('.successfactors.com') || u.hostname.endsWith('.successfactors.eu')) return '';

    // Taleo: {company}.taleo.net/careersection/2/jobdetail.ftl?job=12345 → no title in URL
    if (u.hostname.endsWith('.taleo.net')) return '';

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
 * Processes in concurrent batches of 5 to stay within CDX rate limits
 * while being ~5x faster than serial processing.
 */
export async function buildJobRecords(
  jobSnapshots: CdxSnapshot[],
  startDate?: string,
  endDate?: string,
  delayMs = 800,
  concurrency = 5,
): Promise<JobRecord[]> {
  const records: JobRecord[] = [];
  const total = jobSnapshots.length;

  for (let i = 0; i < total; i += concurrency) {
    const batch = jobSnapshots.slice(i, i + concurrency);
    log.debug(`Lifespan batch [${i + 1}–${Math.min(i + concurrency, total)}/${total}]`);

    const results = await Promise.all(
      batch.map(snap => fetchUrlLifespan(snap.original, startDate, endDate).catch(() => null)),
    );

    for (let j = 0; j < batch.length; j++) {
      const snap = batch[j];
      const lifespan = results[j];
      if (!lifespan) continue;

      const firstDate = cdxDateToIso(lifespan.first);
      const lastDate  = cdxDateToIso(lifespan.last);
      const duration  = daysBetween(firstDate, lastDate);
      const title = titleFromUrl(snap.original) || snap.original;
      const { score, reason } = scoreGhost(duration, lifespan.count, lifespan.reposted, lifespan.repostCount);

      records.push({
        title,
        url: snap.original,
        firstSeen: firstDate,
        lastSeen: lastDate,
        durationDays: duration,
        archiveCount: lifespan.count,
        reposted: lifespan.reposted,
        repostCount: lifespan.repostCount,
        ghostScore: score,
        ghostReason: reason,
      });
    }

    // Delay between batches to respect CDX rate limits
    if (i + concurrency < total) await sleep(delayMs * concurrency);
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
