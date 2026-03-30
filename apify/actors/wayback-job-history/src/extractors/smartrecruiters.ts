import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface SRJob {
  id: string;
  name: string;
  location?: {
    city?: string;
    country?: string;
    countryCode?: string;
    region?: string;
    remote?: boolean;
  };
  department?: { label?: string };
  typeOfEmployment?: { label?: string };
  ref?: string;
}

interface SRResponse {
  content?: SRJob[];
  totalFound?: number;
}

/**
 * Detect SmartRecruiters company ID from a URL.
 * Handles:
 *   careers.smartrecruiters.com/{company}
 *   jobs.smartrecruiters.com/{company}
 */
export function extractSRCompany(url: URL): string | null {
  if (
    url.hostname === 'careers.smartrecruiters.com' ||
    url.hostname === 'jobs.smartrecruiters.com'
  ) {
    const company = url.pathname.split('/').filter(Boolean)[0];
    return company ?? null;
  }
  return null;
}

/**
 * Fetch jobs from the SmartRecruiters public API via the Wayback Machine.
 * API: GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=200
 */
export async function extractFromSmartRecruiters(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const company = extractSRCompany(url);
  if (!company) return { jobs: [], method: 'smartrecruiters-api' };

  const baseApiUrl = `https://api.smartrecruiters.com/v1/companies/${company}/postings?limit=200`;
  log.debug(`Trying SmartRecruiters API via Wayback: ${baseApiUrl}`);

  const data = await fetchArchivedJson<SRResponse>(timestamp, baseApiUrl);
  let rawJobs = data?.content ?? [];
  if (rawJobs.length === 0) return { jobs: [], method: 'smartrecruiters-api' };

  // Paginate if total > 200 (large companies like Deloitte, Bosch)
  const total = data?.totalFound ?? 0;
  if (total > 200 && rawJobs.length === 200) {
    for (let offset = 200; offset < Math.min(total, 1000); offset += 200) {
      const page = await fetchArchivedJson<SRResponse>(timestamp, `${baseApiUrl}&offset=${offset}`);
      if (!page?.content?.length) break;
      rawJobs = [...rawJobs, ...page.content];
      if (page.content.length < 200) break;
    }
  }

  const jobs: JobPosting[] = rawJobs.map(j => {
    const loc = j.location;
    const parts: string[] = [];
    if (loc?.remote) parts.push('Remote');
    else {
      if (loc?.city) parts.push(loc.city);
      if (loc?.region) parts.push(loc.region);
      if (loc?.country) parts.push(loc.country);
    }
    return {
      title: j.name,
      location: parts.length > 0 ? parts.join(', ') : undefined,
      department: j.department?.label,
      url: j.ref ?? `https://jobs.smartrecruiters.com/${company}/${j.id}`,
      id: j.id,
      employmentType: j.typeOfEmployment?.label,
    };
  });

  log.info(`SmartRecruiters API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'smartrecruiters-api' };
}
