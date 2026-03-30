import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface PersonioJob {
  id: number | string;
  name?: string;  // some API versions use 'name'
  jobTitle?: string;  // others use 'jobTitle'
  department?: string | { name?: string };
  office?: string | { name?: string };
  employmentType?: string;
  subcompany?: string;
}

interface PersonioResponse {
  jobs?: PersonioJob[];
  data?: PersonioJob[];
}

/**
 * Detect Personio company slug from a URL.
 * Handles:
 *   {company}.jobs.personio.de
 *   {company}.jobs.personio.com
 */
export function extractPersonioSlug(url: URL): string | null {
  const hostname = url.hostname;
  if (hostname.endsWith('.jobs.personio.de') || hostname.endsWith('.jobs.personio.com')) {
    const slug = hostname.split('.')[0];
    return slug && slug.length >= 2 ? slug : null;
  }
  return null;
}

/**
 * Fetch jobs from the Personio public API via the Wayback Machine.
 * API: GET https://{company}.jobs.personio.de/xml  (or /json endpoint)
 */
export async function extractFromPersonio(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractPersonioSlug(url);
  if (!slug) return { jobs: [], method: 'personio-api' };

  const tld = url.hostname.endsWith('.personio.de') ? 'personio.de' : 'personio.com';
  const apiUrl = `https://${slug}.jobs.${tld}/json`;
  log.debug(`Trying Personio API via Wayback: ${apiUrl}`);

  const data = await fetchArchivedJson<PersonioResponse | PersonioJob[]>(timestamp, apiUrl);

  const rawJobs: PersonioJob[] = Array.isArray(data)
    ? data
    : ((data as PersonioResponse)?.jobs ?? (data as PersonioResponse)?.data ?? []);

  if (rawJobs.length === 0) return { jobs: [], method: 'personio-api' };

  const jobs: JobPosting[] = rawJobs.map(j => {
    const title = j.name ?? j.jobTitle ?? '';
    if (!title) return null;

    const dept = typeof j.department === 'object' ? j.department?.name : j.department;
    const office = typeof j.office === 'object' ? j.office?.name : j.office;

    return {
      title,
      location: office,
      department: dept,
      url: `https://${slug}.jobs.${tld}/job/${j.id}`,
      id: String(j.id),
      employmentType: j.employmentType,
    } as JobPosting;
  }).filter((j): j is JobPosting => j !== null && j.title.length > 0);

  log.info(`Personio API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'personio-api' };
}
