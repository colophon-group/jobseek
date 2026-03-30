import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface TeamtailorJob {
  id: string;
  type?: string;
  attributes?: {
    title?: string;
    'employment-type'?: string;
    'remote-status'?: string;
    pitch?: string;
  };
  relationships?: {
    department?: { data?: { id?: string } };
    location?: { data?: { id?: string } | null };
  };
}

interface TeamtailorResponse {
  data?: TeamtailorJob[];
  included?: Array<{
    id: string;
    type: string;
    attributes?: { name?: string; city?: string };
  }>;
}

/**
 * Detect Teamtailor company slug from a URL.
 * Handles: {company}.teamtailor.com
 */
export function extractTeamtailorSlug(url: URL): string | null {
  const hostname = url.hostname;
  if (hostname.endsWith('.teamtailor.com')) {
    const slug = hostname.replace('.teamtailor.com', '');
    return slug && slug !== 'www' && slug !== 'app' ? slug : null;
  }
  return null;
}

/**
 * Fetch jobs from the Teamtailor public JSON:API via the Wayback Machine.
 * API: GET https://{company}.teamtailor.com/api/v1/jobs?page[number]=1&page[size]=30&include=department,location
 *      (or via /jobs.json for simpler responses)
 * Paginates up to 10 pages to capture large company job lists.
 */
export async function extractFromTeamtailor(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractTeamtailorSlug(url);
  if (!slug) return { jobs: [], method: 'teamtailor-api' };

  // Try the JSON:API endpoint with pagination first (supports large job lists)
  const allIncluded: NonNullable<TeamtailorResponse['included']> = [];
  let rawJobs: TeamtailorJob[] = [];

  for (let page = 1; page <= 10; page++) {
    const pagedUrl = `https://${slug}.teamtailor.com/api/v1/jobs?page%5Bnumber%5D=${page}&page%5Bsize%5D=30&include=department,location`;
    log.debug(`Trying Teamtailor API via Wayback: ${pagedUrl} (page ${page})`);
    const data = await fetchArchivedJson<TeamtailorResponse>(timestamp, pagedUrl);
    if (!data?.data?.length) break;
    rawJobs.push(...data.data);
    if (data.included) allIncluded.push(...data.included);
    if (data.data.length < 30) break; // last page
  }

  // Fallback: legacy /jobs.json endpoint (no pagination, but widely archived)
  if (rawJobs.length === 0) {
    const legacyUrl = `https://${slug}.teamtailor.com/jobs.json`;
    log.debug(`Teamtailor fallback to /jobs.json for ${slug}`);
    const data = await fetchArchivedJson<TeamtailorResponse | TeamtailorJob[]>(timestamp, legacyUrl);
    if (Array.isArray(data)) {
      rawJobs = data;
    } else if (data && 'data' in data && Array.isArray((data as TeamtailorResponse).data)) {
      rawJobs = (data as TeamtailorResponse).data ?? [];
      if ((data as TeamtailorResponse).included) allIncluded.push(...((data as TeamtailorResponse).included ?? []));
    }
  }

  const included = allIncluded;

  if (rawJobs.length === 0) return { jobs: [], method: 'teamtailor-api' };

  // Build lookup for included resources
  const locationMap = new Map<string, string>();
  const deptMap = new Map<string, string>();
  for (const inc of included) {
    if (inc.type === 'locations') {
      const locName = inc.attributes?.city ?? inc.attributes?.name;
      if (locName) locationMap.set(inc.id, locName);
    }
    if (inc.type === 'departments' && inc.attributes?.name) {
      deptMap.set(inc.id, inc.attributes.name);
    }
  }

  const jobs: JobPosting[] = rawJobs.map(j => {
    const attrs = j.attributes ?? (j as unknown as { title?: string });
    const title =
      (attrs as typeof j.attributes)?.title ??
      (j as unknown as { title?: string }).title ?? '';

    if (!title) return null;

    const locId = j.relationships?.location?.data?.id;
    const deptId = j.relationships?.department?.data?.id;
    const remoteStatus = (attrs as typeof j.attributes)?.['remote-status'];
    const isRemote = remoteStatus === 'remote' || remoteStatus === 'hybrid';

    const location = isRemote
      ? (remoteStatus === 'hybrid' ? 'Hybrid' : 'Remote')
      : (locId ? locationMap.get(locId) : undefined);

    return {
      title,
      location,
      department: deptId ? deptMap.get(deptId) : undefined,
      url: `https://${slug}.teamtailor.com/jobs/${j.id}`,
      id: j.id,
      employmentType: (attrs as typeof j.attributes)?.['employment-type'],
    } as JobPosting;
  }).filter((j): j is JobPosting => j !== null && j.title.length > 0);

  log.info(`Teamtailor API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'teamtailor-api' };
}
