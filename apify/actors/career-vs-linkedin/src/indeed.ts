import { log } from 'apify';
import { load } from 'cheerio';
import { fetchCdxSnapshots } from './cdx.js';
import { fetchArchivedPage } from './fetch.js';
import { normalizeTitle } from './match.js';
import type { JobSighting } from './types.js';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

/**
 * Build Indeed URL variants to try for CDX searches.
 * indeed.com/cmp/{slug}/jobs is reliably crawled by Wayback Machine.
 */
export function buildIndeedUrls(slug: string): string[] {
  return [
    `https://www.indeed.com/cmp/${slug}/jobs`,
    `https://www.indeed.com/cmp/${slug}/jobs?start=0`,
  ];
}

/**
 * Collect all unique jobs seen on Indeed for a company over a date range.
 *
 * Strategy:
 * 1. Fetch CDX snapshots of the company's Indeed jobs page and extract job listings.
 * 2. Collect unique individual Indeed job page URLs (/viewjob?jk=...) via page links.
 * 3. Fetch each individual job page to extract title, datePosted, hiringOrganization.
 *
 * Returns a map of normalizedTitle → JobSighting (first sighting only).
 */
export async function collectIndeedJobs(
  companyName: string,
  indeedSlug: string | undefined,
  startDate: string,
  endDate: string,
  maxSnapshots: number,
  delayMs: number,
): Promise<{ jobs: Map<string, JobSighting>; snapshotsProcessed: number; boardUrl: string }> {
  const jobs = new Map<string, JobSighting>();
  let snapshotsProcessed = 0;

  const slug = indeedSlug ?? companyName.replace(/\s+/g, '-');
  const indeedUrls = buildIndeedUrls(slug);
  const primaryUrl = indeedUrls[0];

  // ── Step 1: Company jobs page snapshots ────────────────────────────────────
  let companyPageSnapshots: import('./types.js').CdxSnapshot[] = [];
  for (const url of indeedUrls) {
    const snaps = await fetchCdxSnapshots({ url, startDate, endDate, maxSnapshots: Math.min(maxSnapshots, 50) });
    if (snaps.length > 0) { companyPageSnapshots = snaps; break; }
  }
  log.info(`Indeed company page: ${companyPageSnapshots.length} snapshots`, { url: primaryUrl });

  const jobViewUrls = new Set<string>();

  for (let i = 0; i < companyPageSnapshots.length; i++) {
    const snap = companyPageSnapshots[i];
    const date = tsToDate(snap.timestamp);
    log.info(`[indeed-page ${i + 1}/${companyPageSnapshots.length}] ${date}`);
    const html = await fetchArchivedPage(snap.timestamp, snap.original);
    if (html) {
      const extracted = extractFromIndeedHtml(html, snap, companyName);
      snapshotsProcessed++;
      for (const job of extracted.jobs) mergeJob(jobs, job);
      for (const url of extracted.jobViewUrls) jobViewUrls.add(url);
    }
    if (i < companyPageSnapshots.length - 1) await sleep(delayMs);
  }

  log.info(`Indeed: ${jobViewUrls.size} individual job view URLs to fetch`);

  // ── Step 2: Fetch individual job pages for datePosted ─────────────────────
  const jobViewArray = Array.from(jobViewUrls).slice(0, maxSnapshots * 2);
  log.info(`Indeed: fetching ${jobViewArray.length} individual job pages`);

  for (let i = 0; i < jobViewArray.length; i++) {
    const jobUrl = jobViewArray[i];
    const snaps = await fetchCdxSnapshots({
      url: jobUrl,
      startDate,
      endDate,
      maxSnapshots: 1,
      collapse: 'timestamp:8',
    });
    if (snaps.length === 0) { await sleep(delayMs / 2); continue; }

    const snap = snaps[0];
    const html = await fetchArchivedPage(snap.timestamp, snap.original);
    if (html) {
      snapshotsProcessed++;
      const job = extractFromIndeedJobPage(html, snap, companyName);
      if (job) mergeJob(jobs, job);
    }
    if (i < jobViewArray.length - 1) await sleep(delayMs);
  }

  log.info(`Indeed: ${jobs.size} unique jobs collected`, { company: companyName });
  return { jobs, snapshotsProcessed, boardUrl: primaryUrl };
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

// ── Indeed company jobs page HTML extractor ──────────────────────────────────

interface CompanyPageResult {
  jobs: JobSighting[];
  jobViewUrls: string[];
}

function extractFromIndeedHtml(
  html: string,
  snap: { timestamp: string; original: string },
  companyName: string,
): CompanyPageResult {
  const $ = load(html);
  const jobs: JobSighting[] = [];
  const jobViewUrls: string[] = [];
  const date = tsToDate(snap.timestamp);
  const snapshotUrl = `https://web.archive.org/web/${snap.timestamp}/${snap.original}`;

  // Collect all /viewjob?jk= hrefs found on the page
  $('a[href*="/viewjob"]').each((_, el) => {
    const href = $(el).attr('href') ?? '';
    const match = href.match(/[?&]jk=([a-f0-9]{16})/i);
    if (match) {
      const cleanUrl = `https://www.indeed.com/viewjob?jk=${match[1]}`;
      jobViewUrls.push(cleanUrl);
    }
  });

  // Also collect /jobs/view/ style URLs (newer Indeed format)
  $('a[href*="/jobs/"]').each((_, el) => {
    const href = $(el).attr('href') ?? '';
    const match = href.match(/\/jobs\/[^?#]+jk=([a-f0-9]{16})/i);
    if (match) {
      const cleanUrl = `https://www.indeed.com/viewjob?jk=${match[1]}`;
      jobViewUrls.push(cleanUrl);
    }
  });

  // Extract job titles — multiple Indeed HTML eras
  const titleSelectors = [
    // New Indeed (2022+)
    '[data-testid="job-title"]',
    'h2.jobTitle a span',
    'h2.jobTitle span[title]',
    // Mid-era (2019–2022)
    '.jobCard_mainContent h2 a',
    '.title a',
    '.cmp-JobCard-title',
    '.icl-u-textBold',
    // Older Indeed
    '.jobtitle a',
    '.jobtitle span',
    'h2.title a',
    // Generic fallback
    'h2[class*="job"] a',
    'h2[class*="title"] a',
  ];

  const seenTitles = new Set<string>();
  for (const sel of titleSelectors) {
    $(sel).each((_, el) => {
      const title = $(el).attr('title')?.trim() || $(el).text().trim();
      if (!title || seenTitles.has(title)) return;
      seenTitles.add(title);
      const normTitle = normalizeTitle(title);
      if (!normTitle) return;
      jobs.push({
        title,
        normalizedTitle: normTitle,
        firstSeen: date,
        snapshotUrl,
        platform: 'indeed',
        extractionMethod: 'indeed-company-page-html',
      });
    });
  }

  // Try JSON-LD on the listing page
  $('script[type="application/ld+json"]').each((_, el) => {
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
        if (!title || seenTitles.has(title)) continue;
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
          platform: 'indeed',
          extractionMethod: 'indeed-jsonld',
        });
      }
    } catch { /* skip */ }
  });

  return { jobs, jobViewUrls };
}

// ── Indeed individual job page extractor ─────────────────────────────────────

function extractFromIndeedJobPage(
  html: string,
  snap: { timestamp: string; original: string },
  companyName: string,
): JobSighting | null {
  const $ = load(html);
  const date = tsToDate(snap.timestamp);
  const snapshotUrl = `https://web.archive.org/web/${snap.timestamp}/${snap.original}`;

  // 1. JSON-LD — Indeed populates this for crawlers
  let extracted: JobSighting | null = null;
  $('script[type="application/ld+json"]').each((_, el) => {
    if (extracted) return;
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
          platform: 'indeed',
          extractionMethod: 'indeed-job-page-jsonld',
        };
      }
    } catch { /* skip */ }
  });
  if (extracted) return extracted;

  // 2. HTML selectors — Indeed job page structure varies by era
  const titleEl = $(
    'h1[class*="jobsearch-JobInfoHeader-title"], h1.icl-u-xs-mb--xs, h1[data-testid="jobsearch-JobInfoHeader-title"],'
    + ' h1.jobsearch-JobInfoHeader-title, .jobsearch-JobInfoHeader-title span'
  ).first();
  const companyEl = $(
    '[data-testid="inlineHeader-companyName"] a, .jobsearch-InlineCompanyRating-companyHeader a,'
    + ' .jobsearch-CompanyInfoContainer a, .icl-u-lg-mr--sm.icl-u-xs-mr--sm a'
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
    platform: 'indeed',
    extractionMethod: 'indeed-job-page-html',
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function getHiringOrganization(obj: Record<string, unknown>): string | null {
  const ho = obj['hiringOrganization'];
  if (!ho || typeof ho !== 'object') return null;
  return String((ho as Record<string, unknown>)['name'] ?? '').trim() || null;
}

function orgMatchesCompany(org: string, company: string): boolean {
  const normalize = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  return normalize(org).includes(normalize(company)) || normalize(company).includes(normalize(org));
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
