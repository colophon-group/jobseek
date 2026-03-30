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

  // Try multiple API endpoints — newer Personio deployments may use different paths
  const apiUrls = [
    `https://${slug}.jobs.${tld}/json`,
    `https://${slug}.jobs.${tld}/api/v1/positions`,
    `https://${slug}.jobs.${tld}/en/jobs.json`,
  ];

  let rawJobs: PersonioJob[] = [];
  for (const apiUrl of apiUrls) {
    log.debug(`Trying Personio API via Wayback: ${apiUrl}`);
    const data = await fetchArchivedJson<PersonioResponse | PersonioJob[]>(timestamp, apiUrl);
    if (!data) continue;
    rawJobs = Array.isArray(data)
      ? data
      : ((data as PersonioResponse)?.jobs ?? (data as PersonioResponse)?.data ?? []);
    if (rawJobs.length > 0) break;
  }

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

// ── SAP SuccessFactors ────────────────────────────────────────────────────────
// Enterprise ATS/HCM with 5000+ tenants. Pattern: {tenant}.successfactors.com/careers

const SF_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'cdn', 'secure', 'sso', 'hcm', 'preview', 'help', 'test', 'staging']);

export function extractSuccessFactorsSlug(url: URL): string | null {
  const h = url.hostname;
  if (!h.endsWith('.successfactors.com') && !h.endsWith('.successfactors.eu')) return null;
  const s = h.split('.')[0].toLowerCase();
  return (!s || SF_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromSuccessFactors(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractSuccessFactorsSlug(url);
  if (!slug) return { jobs: [], method: 'successfactors' };

  const tld = url.hostname.endsWith('.successfactors.eu') ? 'successfactors.eu' : 'successfactors.com';
  const base = `https://${slug}.${tld}`;

  interface SFJob {
    requisitionId?: string | number;
    jobReqId?: string | number;
    jobTitle?: string;
    externalJobTitle?: string;
    title?: string;
    location?: string;
    city?: string;
    state?: string;
    country?: string;
    department?: string;
    jobFamily?: string;
    employmentType?: string;
    contractType?: string;
  }
  interface SFResponse { requisitions?: SFJob[]; results?: SFJob[]; data?: SFJob[]; jobs?: SFJob[] }

  const apiUrls = [
    `${base}/careersection/REST/search/v1?site=1&lang=en_US&rows=100`,
    `${base}/career-site-service/postings?pageNum=0&pageSize=100&lang=en_US`,
    `${base}/careers/REST/search/v1?site=1&lang=en_US&rows=100`,
    `${base}/api/rest/v1/posting/getjobs?lang=en_US`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<SFResponse | SFJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: SFJob[] = Array.isArray(data)
      ? data
      : ((data as SFResponse).requisitions ?? (data as SFResponse).results ?? (data as SFResponse).data ?? (data as SFResponse).jobs ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.externalJobTitle ?? j.jobTitle ?? j.title ?? '';
      if (!title) return [];
      const location = j.location ?? ([j.city, j.state, j.country].filter(Boolean).join(', ') || undefined);
      const id = j.requisitionId ?? j.jobReqId;
      const idStr = id ? String(id) : undefined;
      return [{ title, location, department: j.department ?? j.jobFamily, employmentType: j.employmentType ?? j.contractType, id: idStr, url: idStr ? `${base}/careers/jobdetails.aspx?job_id=${idStr}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`SuccessFactors API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'successfactors-api' }; }
  }
  return { jobs: [], method: 'successfactors' };
}
