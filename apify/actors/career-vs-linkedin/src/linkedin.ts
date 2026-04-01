import { log } from 'apify';
import { load } from 'cheerio';
import { fetchCdxSnapshots } from './cdx.js';
import { fetchArchivedPage } from './fetch.js';
import { normalizeTitle } from './match.js';
import type { JobSighting } from './types.js';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

/**
 * Build the LinkedIn URL(s) to use for CDX searches.
 *
 * Priority:
 * 1. linkedin.com/company/{slug}/jobs/ — most direct, no login needed for crawlers
 * 2. linkedin.com/jobs/search/?f_C={companyId} — numeric ID based search
 */
export function buildLinkedInUrls(slug?: string, companyId?: string): string[] {
  const urls: string[] = [];
  if (slug) {
    // /jobs/ sub-page is most direct; main company page sometimes has job listings too
    urls.push(`https://www.linkedin.com/company/${slug}/jobs/`);
    urls.push(`https://www.linkedin.com/company/${slug}/`);
    urls.push(`https://www.linkedin.com/jobs/search/?f_C=&keywords=&location=&geoId=&f_C=${slug}`);
  }
  if (companyId) {
    urls.push(`https://www.linkedin.com/jobs/search/?f_C=${companyId}`);
    urls.push(`https://www.linkedin.com/jobs/search/?f_C=${companyId}&keywords=&location=`);
  }
  return urls;
}

/**
 * Collect all unique jobs seen on LinkedIn for a company over a date range.
 *
 * Strategy:
 * 1. Fetch CDX snapshots of the company's LinkedIn jobs page and extract job listings.
 * 2. Collect unique individual LinkedIn job page URLs (/jobs/view/{id}/) via CDX inventory.
 * 3. Fetch each individual job page to extract title, datePosted, and hiringOrganization.
 *
 * Returns a map of normalizedTitle → JobSighting (first sighting only).
 */
export async function collectLinkedInJobs(
  companyName: string,
  linkedinSlug: string | undefined,
  linkedinCompanyId: string | undefined,
  startDate: string,
  endDate: string,
  maxSnapshots: number,
  delayMs: number,
): Promise<{ jobs: Map<string, JobSighting>; snapshotsProcessed: number; linkedinUrl: string }> {
  const jobs = new Map<string, JobSighting>();
  let snapshotsProcessed = 0;
  const linkedinUrls = buildLinkedInUrls(linkedinSlug, linkedinCompanyId);
  const primaryLinkedInUrl = linkedinUrls[0] ?? `https://www.linkedin.com/company/${companyName.toLowerCase().replace(/\s+/g, '-')}/jobs/`;

  // ── Step 1: Company jobs page snapshots ────────────────────────────────────
  let companyPageSnapshots: import('./types.js').CdxSnapshot[] = [];
  for (const url of linkedinUrls) {
    const snaps = await fetchCdxSnapshots({ url, startDate, endDate, maxSnapshots: Math.min(maxSnapshots, 50) });
    if (snaps.length > 0) { companyPageSnapshots = snaps; break; }
  }
  log.info(`LinkedIn company page: ${companyPageSnapshots.length} snapshots`, { url: primaryLinkedInUrl });

  // Map jobViewUrl → best known snapshot timestamp (from the page we found it on)
  const jobViewUrls = new Map<string, string>();

  for (let i = 0; i < companyPageSnapshots.length; i++) {
    const snap = companyPageSnapshots[i];
    const date = tsToDate(snap.timestamp);
    log.info(`[linkedin-page ${i + 1}/${companyPageSnapshots.length}] ${date}`);
    const html = await fetchArchivedPage(snap.timestamp, snap.original);
    if (html) {
      const extracted = extractFromLinkedInHtml(html, snap, companyName);
      snapshotsProcessed++;
      for (const job of extracted.jobs) mergeJob(jobs, job);
      // Record the earliest company-page timestamp where this job view URL appeared
      for (const url of extracted.jobViewUrls) {
        if (!jobViewUrls.has(url) || snap.timestamp < jobViewUrls.get(url)!) {
          jobViewUrls.set(url, snap.timestamp);
        }
      }
    }
    if (i < companyPageSnapshots.length - 1) await sleep(delayMs);
  }

  // ── Step 2: Additional LinkedIn URL variants ───────────────────────────────
  if (jobViewUrls.size < 5 && linkedinUrls.length > 1) {
    for (const altUrl of linkedinUrls.slice(1)) {
      const altSnaps = await fetchCdxSnapshots({ url: altUrl, startDate, endDate, maxSnapshots: Math.min(maxSnapshots, 20) });
      for (const snap of altSnaps) {
        const html = await fetchArchivedPage(snap.timestamp, snap.original);
        if (html) {
          const result = extractFromLinkedInHtml(html, snap, companyName);
          for (const job of result.jobs) mergeJob(jobs, job);
          for (const u of result.jobViewUrls) {
            if (!jobViewUrls.has(u) || snap.timestamp < jobViewUrls.get(u)!) {
              jobViewUrls.set(u, snap.timestamp);
            }
          }
          snapshotsProcessed++;
        }
      }
      if (jobViewUrls.size >= 5) break;
    }
  }
  log.info(`LinkedIn: ${jobViewUrls.size} individual job view URLs to fetch`);

  // ── Step 3: Fetch individual job pages ────────────────────────────────────
  // Use the known snapshot timestamp from the company page — this avoids CDX lookup
  // (which times out for LinkedIn job view URLs) and directly fetches the archived page.
  const jobViewArray = Array.from(jobViewUrls.entries()).slice(0, maxSnapshots * 2);
  log.info(`LinkedIn: fetching ${jobViewArray.length} individual job pages`);

  for (let i = 0; i < jobViewArray.length; i++) {
    const [jobUrl, knownTimestamp] = jobViewArray[i];

    // Try direct fetch using the known timestamp first (avoids slow CDX query)
    const snap = { timestamp: knownTimestamp, original: jobUrl };
    // Direct fetch using known company-page timestamp.
    // LinkedIn individual job pages are rarely archived independently — if this
    // returns empty we skip rather than doing a slow CDX lookup that will time out.
    const html = await fetchArchivedPage(snap.timestamp, snap.original);
    if (html) {
      snapshotsProcessed++;
      const job = extractFromLinkedInJobPage(html, snap, companyName);
      if (job) mergeJob(jobs, job);
    }
    if (i < jobViewArray.length - 1) await sleep(delayMs);
  }

  log.info(`LinkedIn: ${jobs.size} unique jobs collected`, { company: companyName });
  return { jobs, snapshotsProcessed, linkedinUrl: primaryLinkedInUrl };
}

function mergeJob(registry: Map<string, JobSighting>, job: JobSighting): void {
  const existing = registry.get(job.normalizedTitle);
  const effectiveDate = job.datePosted ?? job.firstSeen;
  const existingDate = existing ? (existing.datePosted ?? existing.firstSeen) : null;
  if (!existing || effectiveDate < (existingDate ?? '9999')) {
    registry.set(job.normalizedTitle, job);
  }
}

function tsToDate(ts: string): string {
  return `${ts.slice(0, 4)}-${ts.slice(4, 6)}-${ts.slice(6, 8)}`;
}

// ── LinkedIn company jobs page HTML extractor ─────────────────────────────────

interface CompanyPageResult {
  jobs: JobSighting[];
  jobViewUrls: string[];
}

function extractFromLinkedInHtml(html: string, snap: { timestamp: string; original: string }, companyName: string): CompanyPageResult {
  const $ = load(html);
  const jobs: JobSighting[] = [];
  const jobViewUrls: string[] = [];
  const date = tsToDate(snap.timestamp);
  const snapshotUrl = `https://web.archive.org/web/${snap.timestamp}/${snap.original}`;

  // Collect all /jobs/view/ hrefs found on the page
  // LinkedIn URL formats: /jobs/view/12345/ (old) or /jobs/view/title-at-co-12345?tracking (new)
  $('a[href*="/jobs/view/"]').each((_, el) => {
    const href = $(el).attr('href') ?? '';
    const match = href.match(/\/jobs\/view\/(?:[^/?]*?-)?(\d{7,})/);
    if (match) {
      const cleanUrl = `https://www.linkedin.com/jobs/view/${match[1]}/`;
      jobViewUrls.push(cleanUrl);
    }
  });

  // Extract job titles from listing elements (multiple LinkedIn HTML eras)
  const titleSelectors = [
    'h3.job-card-list__title',
    'h3.base-search-card__title',
    'h3.base-main-card__title',
    '.job-result-card__title',
    'h3[class*="job-card"]',
    'h3[class*="base-main-card"]',
    '.jobs-unified-top-card__job-title',
    '[class*="job-title"]',
    '.result-card__title',
  ];

  const seenTitles = new Set<string>();
  for (const sel of titleSelectors) {
    $(sel).each((_, el) => {
      const title = $(el).text().trim();
      if (!title || seenTitles.has(title)) return;
      seenTitles.add(title);
      const normTitle = normalizeTitle(title);
      if (!normTitle) return;
      // Walk up to the job card container and look for a <time datetime="YYYY-MM-DD">
      // LinkedIn embeds its own real posting date here — not the archive snapshot date
      const card = $(el).closest('li, article, div.job-card-container, div.base-card, div[class*="job-card"]');
      const timeEl = card.length ? card.find('time[datetime]').first() : $(el).closest('li').find('time[datetime]').first();
      const linkedinDatePosted = timeEl.attr('datetime')?.slice(0, 10) ?? undefined;
      jobs.push({
        title,
        normalizedTitle: normTitle,
        firstSeen: date,
        datePosted: linkedinDatePosted,
        snapshotUrl,
        platform: 'linkedin',
        extractionMethod: 'linkedin-company-page-html',
      });
    });
  }

  // Try JSON-LD on listing page
  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      const raw = $(el).html() ?? '';
      const data: unknown = JSON.parse(raw);
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        if (!item || typeof item !== 'object') continue;
        const obj = item as Record<string, unknown>;
        if (obj['@type'] === 'JobPosting' || (Array.isArray(obj['@type']) && (obj['@type'] as string[]).includes('JobPosting'))) {
          const title = String(obj['title'] ?? obj['name'] ?? '').trim();
          if (!title || seenTitles.has(title)) continue;
          // Verify this job belongs to our target company
          const org = getHiringOrganization(obj);
          if (org && !orgMatchesCompany(org, companyName)) continue;
          seenTitles.add(title);
          const normTitle = normalizeTitle(title);
          if (!normTitle) continue;
          jobs.push({
            title,
            normalizedTitle: normTitle,
            firstSeen: date,
            datePosted: obj['datePosted'] ? String(obj['datePosted']).slice(0, 10) : undefined,
            snapshotUrl,
            location: extractLocationStr(obj),
            platform: 'linkedin',
            extractionMethod: 'linkedin-jsonld',
          });
        }
      }
    } catch { /* skip */ }
  });

  return { jobs, jobViewUrls };
}

// ── LinkedIn individual job page extractor ────────────────────────────────────

function extractFromLinkedInJobPage(
  html: string,
  snap: { timestamp: string; original: string },
  companyName: string,
): JobSighting | null {
  const $ = load(html);
  const date = tsToDate(snap.timestamp);
  const snapshotUrl = `https://web.archive.org/web/${snap.timestamp}/${snap.original}`;

  // 1. JSON-LD is most reliable — LinkedIn populates it for crawlers
  let extracted: JobSighting | null = null;
  $('script[type="application/ld+json"]').each((_, el) => {
    if (extracted) return; // already found one
    try {
      const raw = $(el).html() ?? '';
      const data: unknown = JSON.parse(raw);
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        if (!item || typeof item !== 'object') continue;
        const obj = item as Record<string, unknown>;
        const type = obj['@type'];
        if (type !== 'JobPosting' && !(Array.isArray(type) && (type as string[]).includes('JobPosting'))) continue;

        const title = String(obj['title'] ?? obj['name'] ?? '').trim();
        if (!title) continue;

        // Verify the job belongs to our target company
        const org = getHiringOrganization(obj);
        if (org && !orgMatchesCompany(org, companyName)) continue;

        const normTitle = normalizeTitle(title);
        if (!normTitle) continue;

        extracted = {
          title,
          normalizedTitle: normTitle,
          firstSeen: date,
          datePosted: obj['datePosted'] ? String(obj['datePosted']).slice(0, 10) : undefined,
          snapshotUrl,
          location: extractLocationStr(obj),
          platform: 'linkedin',
          extractionMethod: 'linkedin-job-page-jsonld',
        };
      }
    } catch { /* skip */ }
  });
  if (extracted) return extracted;

  // 2. HTML selectors — LinkedIn job page structure varies by era
  const titleEl = $(
    'h1.top-card-layout__title, h1[class*="job-title"], h1[class*="topcard__title"], .jobs-unified-top-card__job-title h1, h1.t-24'
  ).first();
  const companyEl = $(
    '.topcard__org-name-link, .top-card-layout__second-subline, [class*="company-name"], .job-details-jobs-unified-top-card__company-name'
  ).first();

  const title = titleEl.text().trim();
  const company = companyEl.text().trim();

  if (!title) return null;
  if (company && !orgMatchesCompany(company, companyName)) return null;

  const normTitle = normalizeTitle(title);
  if (!normTitle) return null;

  return {
    title,
    normalizedTitle: normTitle,
    firstSeen: date,
    snapshotUrl,
    platform: 'linkedin',
    extractionMethod: 'linkedin-job-page-html',
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function getHiringOrganization(obj: Record<string, unknown>): string | null {
  const ho = obj['hiringOrganization'];
  if (!ho || typeof ho !== 'object') return null;
  return String((ho as Record<string, unknown>)['name'] ?? '').trim() || null;
}

function orgMatchesCompany(org: string, company: string): boolean {
  const normalizeOrg = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  return normalizeOrg(org).includes(normalizeOrg(company)) ||
         normalizeOrg(company).includes(normalizeOrg(org));
}

function extractLocationStr(obj: Record<string, unknown>): string | undefined {
  const loc = obj['jobLocation'];
  if (!loc) return undefined;
  const items = Array.isArray(loc) ? loc : [loc];
  const parts: string[] = [];
  for (const l of items) {
    if (typeof l === 'string') { if (l) parts.push(l); }
    else if (l && typeof l === 'object') {
      const lo = l as Record<string, unknown>;
      const addr = lo['address'] as Record<string, unknown> | undefined;
      const part = String(lo['name'] ?? addr?.['addressLocality'] ?? addr?.['addressRegion'] ?? '').trim();
      if (part) parts.push(part);
    }
  }
  return parts.length > 0 ? parts.join(', ') : undefined;
}
