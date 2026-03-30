import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface FactorialJob {
  id?: string | number;
  title?: string;
  name?: string;
  location_id?: string | number;
  location?: string | { name?: string; city?: string; country?: string };
  team?: string | { name?: string };
  schedule_type?: string;
  employment_type?: string;
  remote?: boolean;
  slug?: string;
}

interface FactorialResponse {
  job_postings?: FactorialJob[];
  jobs?: FactorialJob[];
  data?: FactorialJob[];
}

export function extractFactorialSlug(url: URL): string | null {
  if (url.hostname !== 'factorialhr.com' && url.hostname !== 'www.factorialhr.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const jpIdx = parts.indexOf('job_postings');
  if (jpIdx === -1) return null;
  const seg = parts[jpIdx + 1];
  return seg && seg.length >= 2 ? seg.toLowerCase() : null;
}

export async function extractFromFactorial(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractFactorialSlug(url);
  if (!slug) return { jobs: [], method: 'factorial' };

  const apiUrls = [
    `https://factorialhr.com/api/v1/job_postings?company_slug=${slug}`,
    `https://api.factorialhr.com/api/v1/job_postings?company_slug=${slug}`,
  ];

  for (const apiUrl of apiUrls) {
    log.debug(`Trying Factorial API via Wayback: ${apiUrl}`);
    const data = await fetchArchivedJson<FactorialResponse | FactorialJob[]>(ts, apiUrl);
    if (!data) continue;

    const rawJobs: FactorialJob[] = Array.isArray(data)
      ? data
      : ((data as FactorialResponse).job_postings ?? (data as FactorialResponse).jobs ?? (data as FactorialResponse).data ?? []);

    if (rawJobs.length > 0) {
      const jobs: JobPosting[] = rawJobs.flatMap(j => {
        const title = j.title ?? j.name ?? '';
        if (!title) return [];
        const loc = j.location;
        let location: string | undefined;
        if (j.remote) {
          location = 'Remote';
        } else if (typeof loc === 'string') {
          location = loc || undefined;
        } else if (loc && typeof loc === 'object') {
          location = [loc.city, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
        }
        const team = j.team;
        const department = typeof team === 'string' ? team || undefined : team?.name || undefined;
        return [{
          title,
          location,
          department,
          employmentType: j.schedule_type ?? j.employment_type,
          id: j.id ? String(j.id) : undefined,
          url: j.slug
            ? `https://factorialhr.com/job_postings/${slug}/${j.slug}`
            : j.id ? `https://factorialhr.com/job_postings/${slug}` : undefined,
        }];
      });

      if (jobs.length > 0) {
        log.info(`Factorial API: ${jobs.length} jobs via Wayback`);
        return { jobs, method: 'factorial-api' };
      }
    }
  }

  return { jobs: [], method: 'factorial' };
}

// ── Workstream ────────────────────────────────────────────────────────────────

export function extractWorkstreamSlug(url: URL): string | null {
  if (url.hostname !== 'jobs.workstream.us') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const seg = parts[0];
  const reserved = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'apply', 'referral', 'v1', 'v2']);
  if (!seg || seg.length < 2 || reserved.has(seg.toLowerCase())) return null;
  if (/^\d+$/.test(seg) || /^[0-9a-f]{8}-/.test(seg)) return null;
  return seg.toLowerCase();
}

export async function extractFromWorkstream(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractWorkstreamSlug(url);
  if (!slug) return { jobs: [], method: 'workstream' };

  interface WorkstreamJob {
    id?: string | number;
    title?: string;
    name?: string;
    location?: string | { name?: string; city?: string; state?: string; country?: string };
    department?: string | { name?: string };
    employment_type?: string;
    remote?: boolean;
  }
  interface WorkstreamResponse { data?: WorkstreamJob[]; jobs?: WorkstreamJob[]; openings?: WorkstreamJob[] }

  const apiUrls = [
    `https://jobs.workstream.us/api/v1/public/employers/${slug}/jobs`,
    `https://jobs.workstream.us/api/v1/employers/${slug}/openings`,
    `https://jobs.workstream.us/api/${slug}/jobs`,
    `https://api.workstream.us/v1/employers/${slug}/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<WorkstreamResponse | WorkstreamJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: WorkstreamJob[] = Array.isArray(data)
      ? data
      : ((data as WorkstreamResponse).data ?? (data as WorkstreamResponse).jobs ?? (data as WorkstreamResponse).openings ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? '';
      if (!title) return [];
      const loc = j.location;
      let location: string | undefined;
      if (j.remote) {
        location = 'Remote';
      } else if (typeof loc === 'string') {
        location = loc || undefined;
      } else if (loc && typeof loc === 'object') {
        location = [loc.city, loc.state, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      return [{ title, location, department, employmentType: j.employment_type, id: j.id ? String(j.id) : undefined, url: j.id ? `https://jobs.workstream.us/${slug}/${j.id}` : undefined }];
    });
    if (jobs.length > 0) { log.info(`Workstream API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'workstream-api' }; }
  }
  return { jobs: [], method: 'workstream' };
}

// ── Dover ─────────────────────────────────────────────────────────────────────

export function extractDoverSlug(url: URL): string | null {
  if (url.hostname !== 'talent.dover.com' && url.hostname !== 'app.dover.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const reserved = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'apply']);
  let seg: string | undefined;
  if (parts[0] === 'jobs' && parts[1]) {
    seg = parts[1];
  } else if (parts[0] && parts[0] !== 'jobs') {
    seg = parts[0];
  }
  if (!seg || reserved.has(seg.toLowerCase()) || seg.length < 2) return null;
  if (/^\d+$/.test(seg) || /^[0-9a-f]{8}-/.test(seg)) return null;
  return seg.toLowerCase();
}

// ── Freshteam ─────────────────────────────────────────────────────────────────

export function extractFreshteamSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.freshteam.com')) return null;
  const s = url.hostname.replace('.freshteam.com', '').toLowerCase();
  const reserved = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'status', 'docs']);
  return (!s || reserved.has(s) || s.length < 2) ? null : s;
}

export async function extractFromFreshteam(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractFreshteamSlug(url);
  if (!slug) return { jobs: [], method: 'freshteam' };

  interface FreshteamJob {
    id?: string | number;
    title?: string;
    branch_jobs?: Array<{ branch?: { city?: string; state?: string; country_code?: string } }>;
    department?: { name?: string } | string;
    type?: string;
    remote?: boolean;
  }
  interface FreshteamResponse { data?: FreshteamJob[]; jobs?: FreshteamJob[] }

  const apiUrls = [
    `https://${slug}.freshteam.com/api/v1/jobs?status=open&limit=100`,
    `https://${slug}.freshteam.com/api/v1/jobs`,
    `https://${slug}.freshteam.com/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<FreshteamResponse | FreshteamJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: FreshteamJob[] = Array.isArray(data)
      ? data
      : ((data as FreshteamResponse).data ?? (data as FreshteamResponse).jobs ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? '';
      if (!title) return [];
      let location: string | undefined;
      if (j.remote) {
        location = 'Remote';
      } else if (j.branch_jobs?.[0]?.branch) {
        const b = j.branch_jobs[0].branch;
        location = [b.city, b.state, b.country_code].filter(Boolean).join(', ') || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      return [{ title, location, department, employmentType: j.type, id: j.id ? String(j.id) : undefined, url: j.id ? `https://${slug}.freshteam.com/jobs/${j.id}` : undefined }];
    });
    if (jobs.length > 0) { log.info(`Freshteam API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'freshteam-api' }; }
  }
  return { jobs: [], method: 'freshteam' };
}

// ── Eightfold.ai ──────────────────────────────────────────────────────────────
// AI talent intelligence platform used by Fortune 500 enterprises:
// Prudential, Chevron, Koch Industries, Bayer, Bristol Myers Squibb, Booz Allen Hamilton, NTT Data.
// Pattern: careers.eightfold.ai/{company}

const EIGHTFOLD_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'about', 'careers', 'apply', 'search', 'v1', 'v2', 'demo']);

export function extractEightfoldSlug(url: URL): string | null {
  if (url.hostname !== 'careers.eightfold.ai') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const seg = parts[0];
  if (!seg || EIGHTFOLD_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
  if (/^\d+$/.test(seg)) return null;
  return seg.toLowerCase();
}

export async function extractFromEightfold(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractEightfoldSlug(url);
  if (!slug) return { jobs: [], method: 'eightfold' };

  interface EightfoldJob {
    id?: string | number;
    name?: string;
    title?: string;
    location?: string | string[];
    category?: string;
    type?: string;
    tags?: string[];
    is_remote?: boolean;
  }
  interface EightfoldResponse { positions?: EightfoldJob[]; jobs?: EightfoldJob[]; data?: EightfoldJob[] }

  const apiUrls = [
    `https://careers.eightfold.ai/api/apply/v2/jobs?domain=${slug}&start=0&num=100`,
    `https://careers.eightfold.ai/api/apply/v1/jobs?domain=${slug}&num=100`,
    `https://careers.eightfold.ai/${slug}/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<EightfoldResponse | EightfoldJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: EightfoldJob[] = Array.isArray(data)
      ? data
      : ((data as EightfoldResponse).positions ?? (data as EightfoldResponse).jobs ?? (data as EightfoldResponse).data ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.name ?? j.title ?? '';
      if (!title) return [];
      const loc = j.location;
      let location: string | undefined;
      if (j.is_remote) {
        location = 'Remote';
      } else if (Array.isArray(loc)) {
        location = (loc as string[]).filter(Boolean).join(', ') || undefined;
      } else if (typeof loc === 'string') {
        location = loc || undefined;
      }
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department: j.category, employmentType: j.type, id, url: id ? `https://careers.eightfold.ai/${slug}/job/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Eightfold API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'eightfold-api' }; }
  }
  return { jobs: [], method: 'eightfold' };
}

export async function extractFromDover(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractDoverSlug(url);
  if (!slug) return { jobs: [], method: 'dover' };

  interface DoverJob {
    id?: string | number;
    title?: string;
    name?: string;
    location?: string | { name?: string; city?: string; state?: string; country?: string };
    department?: string | { name?: string };
    employment_type?: string;
    is_remote?: boolean;
  }
  interface DoverResponse { results?: DoverJob[]; jobs?: DoverJob[]; data?: DoverJob[] }

  const apiUrls = [
    `https://talent.dover.com/api/v1/${slug}/jobs`,
    `https://talent.dover.com/api/v1/jobs?company=${slug}`,
    `https://app.dover.com/api/v1/${slug}/jobs`,
    `https://api.dover.com/v1/job-boards/${slug}/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<DoverResponse | DoverJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: DoverJob[] = Array.isArray(data)
      ? data
      : ((data as DoverResponse).results ?? (data as DoverResponse).jobs ?? (data as DoverResponse).data ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? '';
      if (!title) return [];
      const loc = j.location;
      let location: string | undefined;
      if (j.is_remote) {
        location = 'Remote';
      } else if (typeof loc === 'string') {
        location = loc || undefined;
      } else if (loc && typeof loc === 'object') {
        location = [loc.city, loc.state, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      return [{ title, location, department, employmentType: j.employment_type, id: j.id ? String(j.id) : undefined, url: j.id ? `https://talent.dover.com/jobs/${slug}` : undefined }];
    });
    if (jobs.length > 0) { log.info(`Dover API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'dover-api' }; }
  }
  return { jobs: [], method: 'dover' };
}

// ── Cornerstone OnDemand ──────────────────────────────────────────────────────
// Fortune 500 enterprise ATS/LMS: Boeing, Adobe, FedEx, UnitedHealth, Lockheed.
// Public career site at {tenant}.csod.com/careers — search API is unauthenticated.

const CSOD_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'cdn', 'secure', 'help', 'blog', 'email', 'info', 'test', 'staging', 'preview', 'uat', 'demo']);

export function extractCornerstoneSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.csod.com')) return null;
  const s = url.hostname.split('.')[0].toLowerCase();
  return (!s || CSOD_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromCornerstone(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractCornerstoneSlug(url);
  if (!slug) return { jobs: [], method: 'cornerstone' };

  interface CsodJob {
    JobReqId?: string | number;
    JobTitle?: string;
    Title?: string;
    JobExternalTitle?: string;
    City?: string;
    State?: string;
    Country?: string;
    LocationName?: string;
    JobFamily?: string;
    Department?: string;
    EmploymentType?: string;
    JobType?: string;
  }
  interface CsodResponse { Data?: CsodJob[]; data?: CsodJob[]; Jobs?: CsodJob[]; jobs?: CsodJob[] }

  const base = `https://${slug}.csod.com`;
  const apiUrls = [
    `${base}/ats/careersite/api/search?skip=0&take=50`,
    `${base}/ats/careersite/api/search?site=1&lang=en-US&skip=0&take=50`,
    `${base}/services/x/applicant/ats/careersite/JobSearch.aspx?site=1&lang=en-US`,
    `${base}/ats/careersite/Search.aspx?lang=en-US&listformat=json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<CsodResponse | CsodJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: CsodJob[] = Array.isArray(data)
      ? data
      : ((data as CsodResponse).Data ?? (data as CsodResponse).data ?? (data as CsodResponse).Jobs ?? (data as CsodResponse).jobs ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.JobExternalTitle ?? j.JobTitle ?? j.Title ?? '';
      if (!title) return [];
      const location = [j.City, j.State, j.Country].filter(Boolean).join(', ') || j.LocationName || undefined;
      const id = j.JobReqId ? String(j.JobReqId) : undefined;
      return [{ title, location, department: j.Department ?? j.JobFamily, employmentType: j.EmploymentType ?? j.JobType, id, url: id ? `${base}/ats/careersite/jobdetails.aspx?id=${id}&site=1` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Cornerstone API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'cornerstone-api' }; }
  }
  return { jobs: [], method: 'cornerstone' };
}

// ── PageUp ─────────────────────────────────────────────────────────────────────
// APAC enterprise ATS: Qantas, ANZ Bank, BHP, Telstra, Australian government agencies/universities.
// Pattern: jobs.pageuppeople.com/{company}/go/

const PAGEUP_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'demo', 'go', 'apply', 'v1', 'v2']);

export function extractPageUpSlug(url: URL): string | null {
  if (url.hostname !== 'jobs.pageuppeople.com') return null;
  const seg = url.pathname.split('/').filter(Boolean)[0];
  return (!seg || PAGEUP_RESERVED.has(seg.toLowerCase()) || seg.length < 2 || /^\d+$/.test(seg)) ? null : seg.toLowerCase();
}

export async function extractFromPageUp(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractPageUpSlug(url);
  if (!slug) return { jobs: [], method: 'pageup' };

  interface PageUpJob {
    id?: string | number;
    title?: string;
    positionTitle?: string;
    location?: string | { name?: string; description?: string };
    category?: string | { name?: string };
    workType?: string;
    employmentType?: string;
    closingDate?: string;
  }
  interface PageUpResponse { jobs?: PageUpJob[]; data?: PageUpJob[]; results?: PageUpJob[] }

  const apiUrls = [
    `https://jobs.pageuppeople.com/${slug}/widget-jobs-list.json`,
    `https://jobs.pageuppeople.com/${slug}/go/api/jobs`,
    `https://api.pageuppeople.com/v1/${slug}/jobs`,
    `https://jobs.pageuppeople.com/${slug}/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<PageUpResponse | PageUpJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: PageUpJob[] = Array.isArray(data)
      ? data
      : ((data as PageUpResponse).jobs ?? (data as PageUpResponse).data ?? (data as PageUpResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.positionTitle ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.description) || undefined;
      const cat = j.category;
      const department = typeof cat === 'string' ? cat || undefined : cat?.name || undefined;
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department, employmentType: j.workType ?? j.employmentType, id, url: id ? `https://jobs.pageuppeople.com/${slug}/go/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`PageUp API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'pageup-api' }; }
  }
  return { jobs: [], method: 'pageup' };
}

// ── Avature ────────────────────────────────────────────────────────────────────
// Fortune 500 talent acquisition CRM: Amazon, LinkedIn, EY, PwC, Deloitte, NASA, JPMorgan.
// Pattern: careers.avature.net/{company}

const AVATURE_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'demo', 'careers', 'search', 'apply', 'about']);

export function extractAvatureSlug(url: URL): string | null {
  if (url.hostname !== 'careers.avature.net') return null;
  const seg = url.pathname.split('/').filter(Boolean)[0];
  return (!seg || AVATURE_RESERVED.has(seg.toLowerCase()) || seg.length < 2 || /^\d+$/.test(seg)) ? null : seg.toLowerCase();
}

export async function extractFromAvature(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractAvatureSlug(url);
  if (!slug) return { jobs: [], method: 'avature' };

  interface AvatureJob {
    id?: string | number;
    title?: string;
    jobTitle?: string;
    location?: string | { name?: string; city?: string };
    department?: string | { name?: string };
    employmentType?: string;
    jobType?: string;
  }
  interface AvatureResponse { jobs?: AvatureJob[]; data?: AvatureJob[]; results?: AvatureJob[]; openings?: AvatureJob[] }

  const apiUrls = [
    `https://careers.avature.net/${slug}/SearchJobPage/openings?format=json`,
    `https://careers.avature.net/${slug}/ListJobs?format=json`,
    `https://careers.avature.net/${slug}/api/jobs`,
    `https://careers.avature.net/${slug}/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<AvatureResponse | AvatureJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: AvatureJob[] = Array.isArray(data)
      ? data
      : ((data as AvatureResponse).openings ?? (data as AvatureResponse).jobs ?? (data as AvatureResponse).data ?? (data as AvatureResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.jobTitle ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.city) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department, employmentType: j.employmentType ?? j.jobType, id, url: id ? `https://careers.avature.net/${slug}/ViewJob/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Avature API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'avature-api' }; }
  }
  return { jobs: [], method: 'avature' };
}

// ── Paycor ─────────────────────────────────────────────────────────────────────
// US payroll/HCM with integrated ATS (formerly Newton Software).
// SMBs/mid-market across healthcare, retail, manufacturing.
// Pattern: {tenant}.paycor.com/career-portal

const PAYCOR_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'my', 'secure', 'corp', 'home', 'blog', 'status']);

export function extractPaycorSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.paycor.com')) return null;
  const s = url.hostname.replace('.paycor.com', '').toLowerCase();
  return (!s || PAYCOR_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromPaycor(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractPaycorSlug(url);
  if (!slug) return { jobs: [], method: 'paycor' };

  interface PaycorJob {
    id?: string | number;
    jobReqId?: string | number;
    title?: string;
    jobTitle?: string;
    location?: string | { name?: string; city?: string; state?: string };
    department?: string | { name?: string };
    employmentType?: string;
    jobType?: string;
    status?: string;
  }
  interface PaycorResponse { jobs?: PaycorJob[]; data?: PaycorJob[]; results?: PaycorJob[]; openings?: PaycorJob[] }

  const base = `https://${slug}.paycor.com`;
  const apiUrls = [
    `${base}/career-portal/api/v1/jobs?status=open`,
    `${base}/career-portal/api/jobs`,
    `${base}/api/career-portal/v1/listActiveJobs.json`,
    `${base}/career-portal/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<PaycorResponse | PaycorJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: PaycorJob[] = Array.isArray(data)
      ? data
      : ((data as PaycorResponse).jobs ?? (data as PaycorResponse).data ?? (data as PaycorResponse).results ?? (data as PaycorResponse).openings ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.jobTitle ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? (loc?.city ? [loc.city, loc.state].filter(Boolean).join(', ') : undefined)) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = (j.id ?? j.jobReqId) ? String(j.id ?? j.jobReqId) : undefined;
      return [{ title, location, department, employmentType: j.employmentType ?? j.jobType, id, url: id ? `${base}/career-portal/job/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Paycor API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'paycor-api' }; }
  }
  return { jobs: [], method: 'paycor' };
}

// ── ClearCompany ───────────────────────────────────────────────────────────────
// US mid-market ATS used by healthcare, education, and growth-stage companies.
// Pattern: {tenant}.clearcompany.com/careers

const CLEARCO_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'demo', 'secure', 'my', 'blog', 'status', 'home']);

export function extractClearCompanySlug(url: URL): string | null {
  if (!url.hostname.endsWith('.clearcompany.com')) return null;
  const s = url.hostname.replace('.clearcompany.com', '').toLowerCase();
  return (!s || CLEARCO_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromClearCompany(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractClearCompanySlug(url);
  if (!slug) return { jobs: [], method: 'clearcompany' };

  interface ClearJob {
    id?: string | number;
    jobId?: string | number;
    title?: string;
    jobTitle?: string;
    location?: string | { name?: string; city?: string; state?: string };
    department?: string | { name?: string };
    employmentType?: string;
    jobType?: string;
    status?: string;
  }
  interface ClearResponse { jobs?: ClearJob[]; data?: ClearJob[]; results?: ClearJob[]; positions?: ClearJob[] }

  const base = `https://${slug}.clearcompany.com`;
  const apiUrls = [
    `${base}/careers/api/v1/jobs?status=open`,
    `${base}/careers/api/jobs`,
    `${base}/careers/jobs.json`,
    `${base}/api/v1/careers/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<ClearResponse | ClearJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: ClearJob[] = Array.isArray(data)
      ? data
      : ((data as ClearResponse).jobs ?? (data as ClearResponse).data ?? (data as ClearResponse).results ?? (data as ClearResponse).positions ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.jobTitle ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? (loc?.city ? [loc.city, loc.state].filter(Boolean).join(', ') : undefined)) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = (j.id ?? j.jobId) ? String(j.id ?? j.jobId) : undefined;
      return [{ title, location, department, employmentType: j.employmentType ?? j.jobType, id, url: id ? `${base}/careers/job/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`ClearCompany API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'clearcompany-api' }; }
  }
  return { jobs: [], method: 'clearcompany' };
}

// ── Dayforce HCM (Ceridian) ────────────────────────────────────────────────────
// Enterprise HCM/ATS. Pattern: www.dayforcehcm.com/CandidatePortal/{locale}/{tenant}

const DAYFORCE_RESERVED = new Set([
  'Content', 'api', 'scripts', 'styles', 'images', 'img', 'fonts', 'js', 'css',
  'en-us', 'en-ca', 'fr-ca', 'es-us', 'en-au', 'en-gb',
  'login', 'support', 'help', 'admin', 'demo', 'www', 'app',
]);

export function extractDayforceSlug(url: URL): string | null {
  if (url.hostname !== 'www.dayforcehcm.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  // /CandidatePortal/{locale}/{tenant}/...
  if (parts[0] !== 'CandidatePortal' || !parts[1] || !parts[2]) return null;
  if (DAYFORCE_RESERVED.has(parts[1])) return null;
  const tenant = parts[2].toLowerCase();
  if (!tenant || DAYFORCE_RESERVED.has(tenant) || tenant.length < 2) return null;
  if (/^\d+$/.test(tenant) || /^[0-9a-f]{8}-/.test(tenant)) return null;
  return tenant;
}

export async function extractFromDayforce(url: URL, ts: string): Promise<ExtractionResult> {
  const tenant = extractDayforceSlug(url);
  if (!tenant) return { jobs: [], method: 'dayforce' };

  // Try Dayforce candidate portal API
  // API pattern: /CandidatePortal/en-US/{tenant}/api/jobs or similar
  const base = `https://www.dayforcehcm.com/CandidatePortal/en-US/${tenant}`;

  interface DayforcePosting {
    ReferenceNumber?: string;
    Title?: string;
    ShortDescription?: string;
    Location?: string;
    City?: string;
    State?: string;
    Country?: string;
    JobFunction?: string;
    EmploymentType?: string;
    IsFullTime?: boolean;
  }
  interface DayforceResponse { Items?: DayforcePosting[]; Data?: DayforcePosting[]; Postings?: DayforcePosting[] }

  const apiUrls = [
    `${base}/api/PostingList`,
    `${base}/api/jobs`,
    `${base}/api/Postings`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<DayforceResponse | DayforcePosting[]>(ts, apiUrl);
    if (!data) continue;
    const raw: DayforcePosting[] = Array.isArray(data)
      ? data
      : ((data as DayforceResponse).Items ?? (data as DayforceResponse).Data ?? (data as DayforceResponse).Postings ?? []);
    if (!raw.length) continue;
    const jobs: JobPosting[] = raw.flatMap(j => {
      const title = j.Title ?? j.ShortDescription ?? '';
      if (!title) return [];
      const loc = [j.City, j.State, j.Country].filter(Boolean).join(', ') || j.Location || undefined;
      const id = j.ReferenceNumber;
      return [{ title, location: loc, department: j.JobFunction, employmentType: j.EmploymentType, id, url: id ? `${base}/Posting/View/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Dayforce API: ${jobs.length} jobs`); return { jobs, method: 'dayforce-api' }; }
  }
  return { jobs: [], method: 'dayforce' };
}
