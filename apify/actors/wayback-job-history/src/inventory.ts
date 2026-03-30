import { log } from 'apify';
import type { CdxSnapshot, JobRecord } from './types.js';
import { scoreGhost } from './ghost.js';

/**
 * Fetch ALL archived URLs under a portal via CDX prefix/domain search.
 * One record per unique URL (collapse=urlkey), with earliest timestamp.
 * Supports multi-page CDX pagination via showResumeKey for large portals.
 */
export async function fetchUrlInventory(
  baseUrl: string,
  startDate?: string,
  endDate?: string,
  limit = 5000,
  maxPages = 3,
): Promise<CdxSnapshot[]> {
  const url = new URL(baseUrl);
  // Use domain/* prefix to catch all paths including Workday subpaths
  const searchUrl = `${url.protocol}//${url.hostname}${url.pathname}`;
  const cdxUrlParam = searchUrl + (searchUrl.endsWith('/') ? '*' : '/*');

  const baseParams = new URLSearchParams({
    url: cdxUrlParam,
    matchType: 'prefix',
    output: 'json',
    fl: 'timestamp,original,statuscode',
    filter: 'statuscode:200',
    collapse: 'urlkey',
    limit: String(limit),
    showResumeKey: 'true',
  });

  if (startDate) baseParams.set('from', startDate.replace(/-/g, ''));
  if (endDate)   baseParams.set('to',   endDate.replace(/-/g, ''));

  const allRows: CdxSnapshot[] = [];
  let resumeKey: string | null = null;
  let page = 0;

  while (page < maxPages) {
    const params = new URLSearchParams(baseParams);
    if (resumeKey) params.set('resumeKey', resumeKey);

    const apiUrl = `http://web.archive.org/cdx/search/cdx?${params}`;
    if (page === 0) log.info('CDX inventory search', { apiUrl });

    const raw = await cdxFetchRaw(apiUrl);
    if (!raw || raw.length < 2) break;

    // Check for resume key in last row
    const lastRow = raw[raw.length - 1];
    const hasResumeKey = lastRow?.length === 1 && lastRow[0] !== 'timestamp';
    if (hasResumeKey) {
      resumeKey = lastRow[0];
      raw.splice(-1); // remove resume key row
    } else {
      resumeKey = null;
    }

    // CDX includes header row on every page — filter it out by checking for the header field name
    const dataRows = raw.filter(row => row[0] !== 'timestamp' && row.length >= 2);
    for (const row of dataRows) {
      if (row[1]) {
        allRows.push({ timestamp: row[0], original: row[1] });
      }
    }

    log.info(`CDX inventory p${page}: ${dataRows.length} rows (total: ${allRows.length})`);
    if (!resumeKey) break;
    page++;
    await sleep(1_500);
  }

  log.info(`CDX inventory: ${allRows.length} unique URLs found`);
  return allRows;
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
      /\/job\/[^/]+\/[^/]+-[A-Z0-9]+$/.test(u) ||
      // Fountain: jobs.fountain.com/{company}/{uuid}
      (/jobs\.fountain\.com/.test(u) && /\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(u)) ||
      // Rippling: ats.rippling.com/{company}/jobs/{id}
      (/ats\.rippling\.com/.test(u) && /\/jobs\/[a-z0-9-]{4,}$/i.test(u)) ||
      // Factorial: factorialhr.com/job_postings/{company}/{slug}
      (/factorialhr\.com\/job_postings\/[^/]+\/[a-z0-9-]{4,}/.test(u)) ||
      // Kenjo: app.kenjo.io/{company}/jobs/{id}
      (/app\.kenjo\.io\/[^/]+\/jobs\/\d+/.test(u)) ||
      // Workstream: jobs.workstream.us/{company}/{uuid-or-id}
      (/jobs\.workstream\.us\/[^/]+\/[0-9a-f-]{8,}/.test(u)) ||
      // Dover: talent.dover.com/jobs/{company}/{slug}
      (/talent\.dover\.com\/jobs\/[^/]+\/[a-z0-9-]{4,}/.test(u)) ||
      // Freshteam: {company}.freshteam.com/jobs/{id}
      (/\.freshteam\.com\/jobs\/\d+/.test(u)) ||
      // Jobteaser: jobteaser.com/{locale}/company/{slug}/jobs/{job-ref}
      (/jobteaser\.com\/[a-z]{2}\/company\/[^/]+\/jobs\/[a-z0-9-]{4,}/i.test(u)) ||
      // WTTJ: welcometothejungle.com/{locale}/companies/{slug}/jobs/{job-slug}
      (/welcometothejungle\.com\/[a-z]{2}\/companies\/[^/]+\/jobs\/[a-z0-9-]{4,}/i.test(u)) ||
      // Homerun: {company}.homerun.co/jobs/{id}
      (/\.homerun\.co\/jobs\/[a-z0-9-]{4,}/i.test(u)) ||
      // HiBob: app.hibob.com/careers/{company}/{job-id}
      (/app\.hibob\.com\/careers\/[^/]+\/[a-z0-9-]{4,}/i.test(u)) ||
      // Eightfold: careers.eightfold.ai/{company}/job/{id}
      (/careers\.eightfold\.ai\/[^/]+\/job\/\d+/i.test(u)) ||
      // Cornerstone OnDemand: {tenant}.csod.com/ats/careersite/jobdetails.aspx?id={id}
      (/\.csod\.com\/ats\/careersite\/jobdetails\.aspx/i.test(u)) ||
      // SAP SuccessFactors: {tenant}.successfactors.com/careers/jobdetails.aspx
      (/\.successfactors\.(com|eu)\/careers\/jobdetails\.aspx/i.test(u)) ||
      // PageUp: jobs.pageuppeople.com/{company}/go/{id}
      (/jobs\.pageuppeople\.com\/[^/]+\/go\/\d+/i.test(u)) ||
      // Avature: careers.avature.net/{company}/ViewJob/{id}
      (/careers\.avature\.net\/[^/]+\/ViewJob\/\d+/i.test(u)) ||
      // Hireology: {company}.hireology.com/jobs/{id}
      (/\.hireology\.com\/jobs\/\d+/i.test(u)) ||
      // Zoho Recruit: {company}.zohorecruit.com/jobs/Careers/{id}
      (/\.zohorecruit\.com\/jobs\/Careers\/[a-z0-9-]{4,}/i.test(u)) ||
      // TalentLyft: {company}.talentlyft.com/jobs/{id}
      (/\.talentlyft\.com\/jobs\/[a-z0-9-]{4,}/i.test(u)) ||
      // Occupop: {company}.occupop.com/jobs/{id}
      (/\.occupop\.com\/jobs\/[a-z0-9-]{4,}/i.test(u)) ||
      // Paycor: {tenant}.paycor.com/career-portal/job/{id}
      (/\.paycor\.com\/career-portal\/job\/\d+/i.test(u)) ||
      // ClearCompany: {tenant}.clearcompany.com/careers/job/{id}
      (/\.clearcompany\.com\/careers\/job\/[a-z0-9-]+/i.test(u)) ||
      // Darwinbox: {tenant}.darwinbox.com/ms/candidate/jobs/{id}
      (/\.darwinbox\.(com|in)\/ms\/candidate\/jobs\/\d+/i.test(u)) ||
      // Keka: {tenant}.keka.com/careers/job/{id}
      (/\.keka\.com\/careers\/job\/[a-z0-9-]+/i.test(u)) ||
      // Dayforce HCM: dayforcehcm.com/CandidatePortal/{locale}/{tenant}/Posting/View/{id}
      (/dayforcehcm\.com\/CandidatePortal\/[a-z-]+\/[a-z0-9-]+\/Posting\/View\/\d+/i.test(u)) ||
      // EasyCruit: {company}.easycruit.com/vacancy/{vacancy_id}/{sub_id}
      (/\.easycruit\.com\/vacancy\/\d+/i.test(u)) ||
      // Varbi: {company}.varbi.com/{locale}/what:job/jobID:{id}
      (/\.varbi\.com\/[a-z]+\/what:job\/jobID:\d+/i.test(u))
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

    // Lever: /stripe/abc123-senior-software-engineer (US + EU)
    if ((u.hostname === 'jobs.lever.co' || u.hostname === 'jobs.eu.lever.co') && parts.length >= 2) {
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

    // Cornerstone: {tenant}.csod.com/ats/careersite/jobdetails.aspx?id=123 → no title in URL
    if (u.hostname.endsWith('.csod.com')) return '';

    // Darwinbox: {tenant}.darwinbox.com/ms/candidate/jobs/12345 → no title in URL
    if (u.hostname.endsWith('.darwinbox.com') || u.hostname.endsWith('.darwinbox.in')) return '';

    // Paycor: {tenant}.paycor.com/career-portal/job/12345 → no title in URL
    if (u.hostname.endsWith('.paycor.com')) return '';

    // Dayforce HCM: dayforcehcm.com/CandidatePortal/en-US/{tenant}/Posting/View/12345 → no title in URL
    if (u.hostname === 'www.dayforcehcm.com') return '';

    // Hireology: {tenant}.hireology.com/jobs/12345 → no title in URL
    if (u.hostname.endsWith('.hireology.com')) return '';

    // PageUp: jobs.pageuppeople.com/{company}/go/{id} → numeric ID
    if (u.hostname === 'jobs.pageuppeople.com') return '';

    // Avature: careers.avature.net/{company}/ViewJob/{id} → numeric ID
    if (u.hostname === 'careers.avature.net') return '';

    // EasyCruit: {company}.easycruit.com/vacancy/{id}/{subid} → no title in URL
    if (u.hostname.endsWith('.easycruit.com')) return '';

    // Varbi: {company}.varbi.com/{locale}/what:job/jobID:{id} → no title in URL
    if (u.hostname.endsWith('.varbi.com')) return '';

    // Jobteaser: /en/company/{slug}/jobs/{job-ref} → job-ref is typically a descriptive slug
    if ((u.hostname === 'jobteaser.com' || u.hostname === 'www.jobteaser.com') && parts.indexOf('jobs') >= 0) {
      const jobsIdx = parts.indexOf('jobs');
      const slug = parts[jobsIdx + 1];
      if (slug && slug.length > 4) return slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // WTTJ: /en/companies/{slug}/jobs/{job-slug}
    if ((u.hostname === 'welcometothejungle.com' || u.hostname === 'www.welcometothejungle.com')) {
      const jobsIdx = parts.indexOf('jobs');
      const slug = parts[jobsIdx + 1];
      if (slug && slug.length > 4) {
        // Remove trailing UUID-like hash: "software-engineer-XXXXXX" → "Software Engineer"
        const clean = slug.replace(/-[A-Za-z0-9]{6,8}$/, '');
        return clean.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      }
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

/** Fetch CDX API, return raw rows (including header and optional resume key row). */
async function cdxFetchRaw(apiUrl: string): Promise<string[][] | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(apiUrl, {
        signal: AbortSignal.timeout(60_000),
        headers: { 'Accept': 'application/json' },
      });

      if (res.status === 429) { await sleep(20_000); continue; }
      if (!res.ok) { await sleep(5_000 * (attempt + 1)); continue; }

      const rows: string[][] = await res.json();
      if (!Array.isArray(rows)) return null;
      return rows;
    } catch (err) {
      log.warning(`CDX fetch error (attempt ${attempt + 1}): ${err}`);
      await sleep(5_000 * (attempt + 1));
    }
  }
  return null;
}

async function cdxFetch(apiUrl: string): Promise<CdxSnapshot[]> {
  const raw = await cdxFetchRaw(apiUrl);
  if (!raw || raw.length < 2) return [];
  // Skip header row; map [timestamp, original] columns
  return raw.slice(1)
    .filter(row => row.length >= 2 && row[0] !== undefined)
    .map(([timestamp, original]) => ({ timestamp, original }));
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
