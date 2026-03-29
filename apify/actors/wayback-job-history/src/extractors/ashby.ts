import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface AshbyJob {
  id: string;
  title: string;
  locationName?: string;
  employmentType?: string;
  department?: { name?: string };
  externalLink?: string;
}

interface AshbyJobBoard {
  jobPostings?: AshbyJob[];
  jobs?: AshbyJob[];
}

/**
 * Detect Ashby company slug from a URL.
 * Handles: jobs.ashbyhq.com/{company}
 */
export function extractAshbySlug(url: URL): string | null {
  if (url.hostname === 'jobs.ashbyhq.com') {
    const slug = url.pathname.split('/').filter(Boolean)[0];
    return slug ?? null;
  }
  return null;
}

/**
 * Fetch jobs from the Ashby public API via the Wayback Machine.
 */
export async function extractFromAshby(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractAshbySlug(url);
  if (!slug) return { jobs: [], method: 'ashby-api' };

  // Try the Ashby public job board API
  const apiUrl = `https://api.ashbyhq.com/posting-api/job-board/${slug}?includeCompensation=false`;
  log.debug(`Trying Ashby API via Wayback: ${apiUrl}`);

  const data = await fetchArchivedJson<AshbyJobBoard>(timestamp, apiUrl);
  const rawJobs = data?.jobPostings ?? data?.jobs ?? [];
  if (rawJobs.length === 0) return { jobs: [], method: 'ashby-api' };

  const jobs: JobPosting[] = rawJobs.map(j => ({
    title: j.title,
    location: j.locationName,
    department: j.department?.name,
    url: j.externalLink ?? `https://jobs.ashbyhq.com/${slug}/${j.id}`,
    id: j.id,
    employmentType: j.employmentType,
  }));

  log.info(`Ashby API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'ashby-api' };
}
