import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface BambooJob {
  id: string | number;
  jobOpeningName?: string;
  title?: string;
  employmentType?: string;
  department?: { id?: number; name?: string } | string;
  location?: { city?: string; state?: string; country?: string; remote?: boolean };
  city?: string;
  state?: string;
  country?: string;
  isRemote?: boolean;
}

/**
 * Detect BambooHR company slug from a URL.
 * Handles: {company}.bamboohr.com/jobs  and  {company}.bamboohr.com/careers
 */
export function extractBambooHRSlug(url: URL): string | null {
  const hostname = url.hostname;
  if (hostname.endsWith('.bamboohr.com')) {
    const slug = hostname.replace('.bamboohr.com', '');
    return slug && slug !== 'www' ? slug : null;
  }
  return null;
}

/**
 * Fetch jobs from the BambooHR public careers API via the Wayback Machine.
 * API: GET https://{company}.bamboohr.com/careers/list
 */
export async function extractFromBambooHR(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractBambooHRSlug(url);
  if (!slug) return { jobs: [], method: 'bamboohr-api' };

  const apiUrl = `https://${slug}.bamboohr.com/careers/list`;
  log.debug(`Trying BambooHR API via Wayback: ${apiUrl}`);

  const data = await fetchArchivedJson<BambooJob[]>(timestamp, apiUrl);
  if (!Array.isArray(data) || data.length === 0) return { jobs: [], method: 'bamboohr-api' };

  const jobs: JobPosting[] = data.map(j => {
    const title = j.jobOpeningName ?? j.title ?? '';

    // Location can be nested object or flat fields
    let location: string | undefined;
    if (j.location) {
      if (j.isRemote ?? j.location.remote) {
        location = 'Remote';
      } else {
        const parts = [j.location.city, j.location.state, j.location.country].filter(Boolean);
        location = parts.length > 0 ? parts.join(', ') : undefined;
      }
    } else if (j.city || j.state || j.country) {
      location = [j.city, j.state, j.country].filter(Boolean).join(', ');
    }

    const dept =
      typeof j.department === 'object' ? j.department?.name :
      typeof j.department === 'string' ? j.department : undefined;

    const id = String(j.id);
    return {
      title,
      location,
      department: dept,
      url: `https://${slug}.bamboohr.com/careers/${id}`,
      id,
      employmentType: j.employmentType,
    };
  }).filter(j => j.title.length > 0);

  log.info(`BambooHR API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'bamboohr-api' };
}
