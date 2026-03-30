import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface WorkableJob {
  id: string;
  title: string;
  city?: string;
  country?: string;
  state?: string;
  department?: string;
  url?: string;
  employment_type?: string;
}

interface WorkableResponse {
  results?: WorkableJob[];
  jobs?: WorkableJob[];
}

/**
 * Detect Workable company slug from a URL.
 * Handles: apply.workable.com/{company}, {company}.workable.com
 */
export function extractWorkableSlug(url: URL): string | null {
  if (url.hostname === 'apply.workable.com') {
    const slug = url.pathname.split('/').filter(Boolean)[0];
    return slug ?? null;
  }
  if (url.hostname.endsWith('.workable.com')) {
    return url.hostname.split('.')[0] ?? null;
  }
  return null;
}

/**
 * Fetch jobs from the Workable public API via the Wayback Machine.
 */
export async function extractFromWorkable(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractWorkableSlug(url);
  if (!slug) return { jobs: [], method: 'workable-api' };

  // Try multiple Workable API endpoints (v2 widget, v1, and legacy subdomain)
  const apiUrls = [
    `https://apply.workable.com/api/v2/widget/accounts/${slug}/jobs?details=1`,
    `https://apply.workable.com/api/v3/accounts/${slug}/jobs`,
    `https://${slug}.workable.com/api/v2/widget/accounts/${slug}/jobs`,
  ];

  let rawJobs: WorkableJob[] = [];
  for (const apiUrl of apiUrls) {
    log.debug(`Trying Workable API via Wayback: ${apiUrl}`);
    const data = await fetchArchivedJson<WorkableResponse>(timestamp, apiUrl);
    rawJobs = data?.results ?? data?.jobs ?? [];
    if (rawJobs.length > 0) break;
  }

  if (rawJobs.length === 0) return { jobs: [], method: 'workable-api' };

  const jobs: JobPosting[] = rawJobs.map(j => {
    const parts = [j.city, j.state, j.country].filter(Boolean);
    return {
      title: j.title,
      location: parts.length > 0 ? parts.join(', ') : undefined,
      department: j.department,
      url: j.url ?? `https://apply.workable.com/${slug}/j/${j.id}/`,
      id: j.id,
      employmentType: j.employment_type,
    };
  });

  log.info(`Workable API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'workable-api' };
}
