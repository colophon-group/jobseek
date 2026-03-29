import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface RecruiteeOffer {
  id: number;
  slug: string;
  title: string;
  city?: string;
  country_code?: string;
  remote?: boolean;
  department?: string;
  employment_type_code?: string;
}

interface RecruiteeResponse {
  offers?: RecruiteeOffer[];
}

/**
 * Detect Recruitee company slug from a URL.
 * Handles: {company}.recruitee.com/o/{job}  and  {company}.recruitee.com
 */
export function extractRecruiteeSlug(url: URL): string | null {
  const hostname = url.hostname;
  if (hostname.endsWith('.recruitee.com')) {
    const slug = hostname.replace('.recruitee.com', '');
    return slug && slug !== 'www' && slug !== 'app' ? slug : null;
  }
  return null;
}

/**
 * Fetch jobs from the Recruitee public API via the Wayback Machine.
 * API: GET https://{company}.recruitee.com/api/v1/offers
 */
export async function extractFromRecruitee(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractRecruiteeSlug(url);
  if (!slug) return { jobs: [], method: 'recruitee-api' };

  const apiUrl = `https://${slug}.recruitee.com/api/v1/offers`;
  log.debug(`Trying Recruitee API via Wayback: ${apiUrl}`);

  const data = await fetchArchivedJson<RecruiteeResponse>(timestamp, apiUrl);
  const rawJobs = data?.offers ?? [];
  if (rawJobs.length === 0) return { jobs: [], method: 'recruitee-api' };

  const jobs: JobPosting[] = rawJobs.map(j => {
    const parts: string[] = [];
    if (j.remote) parts.push('Remote');
    else {
      if (j.city) parts.push(j.city);
      if (j.country_code) parts.push(j.country_code.toUpperCase());
    }
    return {
      title: j.title,
      location: parts.length > 0 ? parts.join(', ') : undefined,
      department: j.department,
      url: `https://${slug}.recruitee.com/o/${j.slug}`,
      id: String(j.id),
      employmentType: j.employment_type_code,
    };
  }).filter(j => j.title.length > 0);

  log.info(`Recruitee API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'recruitee-api' };
}
