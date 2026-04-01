import { log } from 'apify';
import { load } from 'cheerio';
import { fetchCdxSnapshots } from './cdx.js';
import { fetchArchivedPage } from './fetch.js';
import { normalizeTitle } from './match.js';
import type { JobSighting } from './types.js';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

/**
 * Collect all unique jobs seen on Glassdoor for a company over a date range.
 *
 * Glassdoor's archived pages embed job data in a __NEXT_DATA__ script tag
 * containing an Apollo cache. Each job listing has:
 *   - header.jobTitleText  — the job title
 *   - header.ageInDays     — days since posting (relative to snapshot date)
 *
 * We compute datePosted = snapshotDate - ageInDays. This gives a real posting
 * date that is independent of Wayback archive timing (unlike LinkedIn firstSeen).
 */
export async function collectGlassdoorJobs(
  companyName: string,
  glassdoorUrl: string,
  startDate: string,
  endDate: string,
  maxSnapshots: number,
  delayMs: number,
): Promise<{ jobs: Map<string, JobSighting>; snapshotsProcessed: number; boardUrl: string }> {
  const jobs = new Map<string, JobSighting>();
  let snapshotsProcessed = 0;

  const snapshots = await fetchCdxSnapshots({ url: glassdoorUrl, startDate, endDate, maxSnapshots });
  log.info(`Glassdoor: ${snapshots.length} snapshots for ${companyName}`, { url: glassdoorUrl });

  for (let i = 0; i < snapshots.length; i++) {
    const snap = snapshots[i];
    const snapDate = tsToDate(snap.timestamp);
    log.info(`[glassdoor ${i + 1}/${snapshots.length}] ${snapDate}`);

    const html = await fetchArchivedPage(snap.timestamp, snap.original);
    if (!html) { if (i < snapshots.length - 1) await sleep(delayMs); continue; }

    snapshotsProcessed++;
    const extracted = extractFromGlassdoorHtml(html, snap, companyName);
    for (const job of extracted) mergeJob(jobs, job);

    if (i < snapshots.length - 1) await sleep(delayMs);
  }

  log.info(`Glassdoor: ${jobs.size} unique jobs collected`, { company: companyName });
  return { jobs, snapshotsProcessed, boardUrl: glassdoorUrl };
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

/**
 * Extract jobs from a Glassdoor archived page.
 *
 * Glassdoor uses Next.js with Apollo. Job listings are in:
 *   __NEXT_DATA__.props.pageProps.apolloCache.ROOT_QUERY
 *     .[jobListings(...)].jobListings[].jobview.header
 *
 * Each header has:
 *   - jobTitleText: string (job title)
 *   - ageInDays: number (days since posting relative to snapshot date)
 *   - locationName: string
 */
function extractFromGlassdoorHtml(
  html: string,
  snap: { timestamp: string; original: string },
  _companyName: string,
): JobSighting[] {
  const $ = load(html);
  const jobs: JobSighting[] = [];
  const snapDate = tsToDate(snap.timestamp);
  const snapshotUrl = `https://web.archive.org/web/${snap.timestamp}/${snap.original}`;

  // Find __NEXT_DATA__ script
  const nextDataScript = $('script#__NEXT_DATA__').html() ?? '';
  if (!nextDataScript) return jobs;

  let nextData: Record<string, unknown>;
  try {
    nextData = JSON.parse(nextDataScript) as Record<string, unknown>;
  } catch {
    return jobs;
  }

  // Navigate: props.pageProps.apolloCache.ROOT_QUERY
  const apolloCache = (nextData as any)?.props?.pageProps?.apolloCache;
  const rootQuery = apolloCache?.ROOT_QUERY;
  if (!rootQuery || typeof rootQuery !== 'object') return jobs;

  // Find the jobListings key (there may be multiple with different filter params)
  const listingKeys = Object.keys(rootQuery).filter(k => k.startsWith('jobListings('));

  for (const key of listingKeys) {
    const listingsContainer = (rootQuery as any)[key];
    const listings: unknown[] = listingsContainer?.jobListings ?? [];

    for (const item of listings) {
      if (!item || typeof item !== 'object') continue;
      const header = (item as any)?.jobview?.header;
      if (!header) continue;

      const title = String(header.jobTitleText ?? '').trim();
      if (!title) continue;

      const normTitle = normalizeTitle(title);
      if (!normTitle) continue;

      // Compute datePosted from ageInDays (relative to snapshot date)
      let datePosted: string | undefined;
      if (typeof header.ageInDays === 'number' && header.ageInDays >= 0) {
        const snapMs = new Date(`${snapDate.slice(0, 4)}-${snapDate.slice(5, 7)}-${snapDate.slice(8, 10)}`).getTime();
        const postMs = snapMs - header.ageInDays * 86_400_000;
        datePosted = new Date(postMs).toISOString().slice(0, 10);
      }

      const location = String(header.locationName ?? '').trim() || undefined;

      jobs.push({
        title,
        normalizedTitle: normTitle,
        firstSeen: snapDate,
        datePosted,
        snapshotUrl,
        location,
        platform: 'glassdoor',
        extractionMethod: 'glassdoor-nextdata-apollo',
      });
    }
  }

  return jobs;
}
