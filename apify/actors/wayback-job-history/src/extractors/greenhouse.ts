import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface GhJob {
  id: number;
  title: string;
  location?: { name?: string };
  departments?: Array<{ name?: string }>;
  absolute_url?: string;
}

interface GhJobsResponse {
  jobs: GhJob[];
  meta?: { total?: number };
}

/**
 * Detect the Greenhouse board token from a URL.
 * Handles: boards.greenhouse.io/{token}, job-boards.greenhouse.io/{token}
 */
export function extractGreenhouseToken(url: URL): string | null {
  if (
    url.hostname === 'boards.greenhouse.io' ||
    url.hostname === 'job-boards.greenhouse.io' ||
    url.hostname === 'boards.eu.greenhouse.io' ||
    url.hostname === 'job-boards.eu.greenhouse.io'
  ) {
    const token = url.pathname.split('/').filter(Boolean)[0];
    return token ?? null;
  }
  return null;
}

/** Returns the correct boards-api host for the given Greenhouse URL (US vs EU). */
function greenhouseApiHost(url: URL): string {
  return url.hostname.includes('.eu.') ? 'boards-api.eu.greenhouse.io' : 'boards-api.greenhouse.io';
}

/**
 * Fetch jobs from the Greenhouse public API via the Wayback Machine.
 */
export async function extractFromGreenhouse(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const token = extractGreenhouseToken(url);
  if (!token) return { jobs: [], method: 'greenhouse-api' };

  // Try with per_page=500 first (Greenhouse supports large limits); fall back to default
  const apiHost = greenhouseApiHost(url);
  const apiUrlLarge = `https://${apiHost}/v1/boards/${token}/jobs?content=false&per_page=500`;
  const apiUrl = `https://${apiHost}/v1/boards/${token}/jobs?content=false`;
  log.debug(`Trying Greenhouse API via Wayback: ${apiUrlLarge}`);

  const data = await fetchArchivedJson<GhJobsResponse>(timestamp, apiUrlLarge) ??
               await fetchArchivedJson<GhJobsResponse>(timestamp, apiUrl);
  if (!data?.jobs?.length) return { jobs: [], method: 'greenhouse-api' };

  const jobs: JobPosting[] = data.jobs.map(job => ({
    title: job.title,
    location: job.location?.name ?? undefined,
    department: job.departments?.[0]?.name ?? undefined,
    url: job.absolute_url ?? `https://boards.greenhouse.io/${token}/jobs/${job.id}`,
    id: String(job.id),
  }));

  log.info(`Greenhouse API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'greenhouse-api' };
}
