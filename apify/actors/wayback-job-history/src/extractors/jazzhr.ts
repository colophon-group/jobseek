import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface JazzHRJob {
  id: string;
  title: string;
  city?: string;
  state?: string;
  zip?: string;
  country?: string;
  employment_type?: string;
  department?: string;
}

interface JazzHRResponse {
  jobs?: JazzHRJob[];
}

/**
 * Detect JazzHR company slug from a URL.
 * Handles: {company}.applytojob.com/apply
 */
export function extractJazzHRSlug(url: URL): string | null {
  const hostname = url.hostname;
  if (hostname.endsWith('.applytojob.com')) {
    const slug = hostname.replace('.applytojob.com', '');
    return slug && slug !== 'www' ? slug : null;
  }
  return null;
}

/**
 * Fetch jobs from the JazzHR public jobs feed via the Wayback Machine.
 * API: GET https://{company}.applytojob.com/apply?format=json
 */
export async function extractFromJazzHR(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractJazzHRSlug(url);
  if (!slug) return { jobs: [], method: 'jazzhr-api' };

  const apiUrl = `https://${slug}.applytojob.com/apply?format=json`;
  log.debug(`Trying JazzHR API via Wayback: ${apiUrl}`);

  const data = await fetchArchivedJson<JazzHRResponse | JazzHRJob[]>(timestamp, apiUrl);

  const rawJobs: JazzHRJob[] = Array.isArray(data)
    ? data
    : (data as JazzHRResponse)?.jobs ?? [];

  if (rawJobs.length === 0) return { jobs: [], method: 'jazzhr-api' };

  const jobs: JobPosting[] = rawJobs.map(j => {
    const parts = [j.city, j.state, j.country].filter(Boolean);
    return {
      title: j.title,
      location: parts.length > 0 ? parts.join(', ') : undefined,
      department: j.department,
      url: `https://${slug}.applytojob.com/apply/${j.id}`,
      id: j.id,
      employmentType: j.employment_type,
    };
  }).filter(j => j.title.length > 0);

  log.info(`JazzHR API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'jazzhr-api' };
}
