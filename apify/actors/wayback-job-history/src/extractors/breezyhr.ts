import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface BreezyJob {
  _id?: string;
  name?: string;
  type?: { name?: string };
  department?: { name?: string };
  location?: { country?: string; city?: string; name?: string; is_remote?: boolean };
  state?: string;
}

export function extractBreezySlug(url: URL): string | null {
  // {company}.breezy.hr
  if (url.hostname.endsWith('.breezy.hr') && url.hostname !== 'app.breezy.hr') {
    const s = url.hostname.replace('.breezy.hr', '');
    return s && s !== 'www' && s.length >= 2 ? s : null;
  }
  // app.breezy.hr/p/{company}/... or app.breezy.hr/p/{company}
  if (url.hostname === 'app.breezy.hr') {
    const parts = url.pathname.split('/').filter(Boolean);
    if (parts[0] === 'p' && parts[1] && parts[1].length >= 2) return parts[1];
  }
  return null;
}

export async function extractFromBreezyHR(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractBreezySlug(url);
  if (!slug) return { jobs: [], method: 'breezyhr-api' };

  // Breezy public API: try /json then /feed (both are archived by Wayback)
  const apiUrls = [
    `https://${slug}.breezy.hr/json`,
    `https://app.breezy.hr/p/${slug}/json`,
  ];
  let data: BreezyJob[] | null = null;
  for (const apiUrl of apiUrls) {
    const raw = await fetchArchivedJson<BreezyJob[] | { positions?: BreezyJob[] }>(ts, apiUrl);
    if (!raw) continue;
    data = Array.isArray(raw) ? raw : (raw as { positions?: BreezyJob[] }).positions ?? [];
    if (data.length > 0) break;
  }
  if (!data?.length) return { jobs: [], method: 'breezyhr-api' };

  const jobs: JobPosting[] = data!.map(j => {
    const title = j.name ?? '';
    const loc = j.location;
    let location: string | undefined;
    if (loc) {
      if (loc.is_remote) location = 'Remote';
      else location = [loc.city, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
    }
    return {
      title,
      location,
      department: j.department?.name,
      employmentType: j.type?.name,
      id: j._id,
      url: j._id ? `https://${slug}.breezy.hr/p/${j._id}` : undefined,
    };
  }).filter(j => j.title.length > 0);

  log.info(`BreezyHR: ${jobs.length} jobs`);
  return { jobs, method: 'breezyhr-api' };
}

// ── Homerun ────────────────────────────────────────────────────────────────────

interface HomerunJob {
  id?: string | number;
  title?: string;
  name?: string;
  location?: string | { city?: string; country?: string; name?: string };
  department?: string | { name?: string };
  employment_type?: string;
  remote?: boolean;
  slug?: string;
}

export function extractHomerunSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.homerun.co')) return null;
  const s = url.hostname.replace('.homerun.co', '');
  const reserved = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin']);
  return (!s || reserved.has(s) || s.length < 2) ? null : s.toLowerCase();
}

export async function extractFromHomerun(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractHomerunSlug(url);
  if (!slug) return { jobs: [], method: 'homerun' };

  const apiUrls = [
    `https://${slug}.homerun.co/jobs.json`,
    `https://${slug}.homerun.co/api/jobs`,
    `https://${slug}.homerun.co/vacancies.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<HomerunJob[] | { jobs?: HomerunJob[]; vacancies?: HomerunJob[] }>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: HomerunJob[] = Array.isArray(data) ? data : (data as { jobs?: HomerunJob[]; vacancies?: HomerunJob[] }).jobs ?? (data as { jobs?: HomerunJob[]; vacancies?: HomerunJob[] }).vacancies ?? [];
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
        location = [loc.city, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = j.id ? String(j.id) : j.slug;
      return [{ title, location, department, employmentType: j.employment_type, id, url: id ? `https://${slug}.homerun.co/jobs/${id}` : undefined } as JobPosting];
    });

    if (jobs.length > 0) {
      log.info(`Homerun: ${jobs.length} jobs`);
      return { jobs, method: 'homerun-api' };
    }
  }

  return { jobs: [], method: 'homerun' };
}

// ── HiBob ─────────────────────────────────────────────────────────────────────
// Modern HRIS/ATS used by JetBrains, monday.com, Wix, Papaya Global, Pleo, Lightspeed.
// Pattern: app.hibob.com/careers/{company}

const HIBOB_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'platform', 'pricing', 'features', 'about']);

export function extractHiBobSlug(url: URL): string | null {
  if (url.hostname !== 'app.hibob.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  if (parts[0] !== 'careers') return null;
  const seg = parts[1];
  return (!seg || HIBOB_RESERVED.has(seg.toLowerCase()) || seg.length < 2) ? null : seg.toLowerCase();
}

export async function extractFromHiBob(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractHiBobSlug(url);
  if (!slug) return { jobs: [], method: 'hibob' };

  interface HiBobJob {
    id?: string | number;
    title?: string;
    name?: string;
    location?: string | { city?: string; state?: string; country?: string; name?: string };
    department?: string | { name?: string };
    type?: string;
    employmentType?: string;
    remote?: boolean;
    isRemote?: boolean;
  }
  interface HiBobResponse { data?: HiBobJob[]; jobs?: HiBobJob[]; positions?: HiBobJob[]; results?: HiBobJob[] }

  const apiUrls = [
    `https://app.hibob.com/api/v1/public/positions?companySlug=${slug}`,
    `https://app.hibob.com/api/v1/company/${slug}/jobs`,
    `https://app.hibob.com/api/v1/jobs?companySlug=${slug}`,
    `https://api.hibob.com/v1/${slug}/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<HiBobResponse | HiBobJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: HiBobJob[] = Array.isArray(data)
      ? data
      : ((data as HiBobResponse).data ?? (data as HiBobResponse).jobs ?? (data as HiBobResponse).positions ?? (data as HiBobResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? '';
      if (!title) return [];
      const loc = j.location;
      let location: string | undefined;
      if (j.remote || j.isRemote) {
        location = 'Remote';
      } else if (typeof loc === 'string') {
        location = loc || undefined;
      } else if (loc && typeof loc === 'object') {
        location = [loc.city, loc.state, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department, employmentType: j.type ?? j.employmentType, id, url: id ? `https://app.hibob.com/careers/${slug}/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`HiBob API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'hibob-api' }; }
  }
  return { jobs: [], method: 'hibob' };
}

// ── Hireology ─────────────────────────────────────────────────────────────────
// Automotive/franchise/retail ATS: {company}.hireology.com/jobs

const HIREOLOGY_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'portal', 'recruiting']);

export function extractHireologySlug(url: URL): string | null {
  if (!url.hostname.endsWith('.hireology.com')) return null;
  const s = url.hostname.replace('.hireology.com', '').toLowerCase();
  return (!s || HIREOLOGY_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromHireology(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractHireologySlug(url);
  if (!slug) return { jobs: [], method: 'hireology' };

  interface HireologyJob { id?: string | number; title?: string; name?: string; location?: string; department?: string; employment_type?: string; is_remote?: boolean }
  interface HireologyResponse { jobs?: HireologyJob[]; data?: HireologyJob[]; results?: HireologyJob[] }

  const apiUrls = [
    `https://${slug}.hireology.com/api/v1/jobs`,
    `https://${slug}.hireology.com/jobs.json`,
    `https://${slug}.hireology.com/api/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<HireologyResponse | HireologyJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: HireologyJob[] = Array.isArray(data)
      ? data
      : ((data as HireologyResponse).jobs ?? (data as HireologyResponse).data ?? (data as HireologyResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? '';
      if (!title) return [];
      const location = j.is_remote ? 'Remote' : (j.location || undefined);
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department: j.department, employmentType: j.employment_type, id, url: id ? `https://${slug}.hireology.com/jobs/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Hireology API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'hireology-api' }; }
  }
  return { jobs: [], method: 'hireology' };
}

// ── Zoho Recruit ──────────────────────────────────────────────────────────────
// Zoho cloud ATS: {company}.zohorecruit.com/jobs/Careers

const ZOHO_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'eu', 'in', 'au']);

export function extractZohoRecruitSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.zohorecruit.com')) return null;
  const s = url.hostname.replace('.zohorecruit.com', '').toLowerCase();
  if (/^(eu|in|au|us|ca)$/.test(s)) return null;
  return (!s || ZOHO_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromZohoRecruit(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractZohoRecruitSlug(url);
  if (!slug) return { jobs: [], method: 'zohorecruit' };

  interface ZohoJob { id?: string | number; title?: string; jobTitle?: string; jobLocation?: string; location?: string; department?: string; jobType?: string }
  interface ZohoResponse { response?: { result?: { JobOpenings?: { row?: Array<{ FL?: Array<{val:string;content:string}> }> }[] } }; jobs?: ZohoJob[]; data?: ZohoJob[] }

  const apiUrls = [
    `https://${slug}.zohorecruit.com/api/v2/JobOpenings?category=Job+Openings&index=0&range=100`,
    `https://${slug}.zohorecruit.com/jobs/rss.xml`,
    `https://${slug}.zohorecruit.com/api/jobs?format=json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<ZohoResponse | ZohoJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: ZohoJob[] = Array.isArray(data)
      ? data
      : ((data as ZohoResponse).jobs ?? (data as ZohoResponse).data ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.jobTitle ?? '';
      if (!title) return [];
      const location = (j.jobLocation ?? j.location) || undefined;
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department: j.department, employmentType: j.jobType, id, url: id ? `https://${slug}.zohorecruit.com/jobs/Careers/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Zoho Recruit API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'zohorecruit-api' }; }
  }
  return { jobs: [], method: 'zohorecruit' };
}

// ── Darwinbox ─────────────────────────────────────────────────────────────────
// India enterprise HCM/ATS: Swiggy, Zomato, Puma, JSW, Bajaj.
// Pattern: {tenant}.darwinbox.com/ms/candidate/jobs

const DARWINBOX_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'hr', 'dev', 'staging', 'uat']);

export function extractDarwinboxSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.darwinbox.com') && !url.hostname.endsWith('.darwinbox.in')) return null;
  const s = url.hostname.replace(/\.(darwinbox\.com|darwinbox\.in)$/, '').toLowerCase();
  return (!s || DARWINBOX_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromDarwinbox(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractDarwinboxSlug(url);
  if (!slug) return { jobs: [], method: 'darwinbox' };

  const tld = url.hostname.endsWith('.darwinbox.in') ? 'darwinbox.in' : 'darwinbox.com';
  const base = `https://${slug}.${tld}`;

  interface DarwinJob { id?: string | number; jobId?: string | number; title?: string; jobTitle?: string; designation?: string; location?: string | { name?: string; city?: string; state?: string }; department?: string | { name?: string }; jobType?: string; employmentType?: string }
  interface DarwinResponse { data?: DarwinJob[]; jobs?: DarwinJob[]; results?: DarwinJob[]; postings?: DarwinJob[] }

  const apiUrls = [
    `${base}/api/v1/jobs?status=open`,
    `${base}/ms/candidate/jobs/json`,
    `${base}/ms/candidate/api/v1/jobs`,
    `${base}/api/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<DarwinResponse | DarwinJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: DarwinJob[] = Array.isArray(data)
      ? data
      : ((data as DarwinResponse).data ?? (data as DarwinResponse).jobs ?? (data as DarwinResponse).results ?? (data as DarwinResponse).postings ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.jobTitle ?? j.designation ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? (loc?.city ? [loc.city, loc.state].filter(Boolean).join(', ') : undefined)) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = (j.id ?? j.jobId) ? String(j.id ?? j.jobId) : undefined;
      return [{ title, location, department, employmentType: j.jobType ?? j.employmentType, id, url: id ? `${base}/ms/candidate/jobs/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Darwinbox API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'darwinbox-api' }; }
  }
  return { jobs: [], method: 'darwinbox' };
}

// ── Keka ──────────────────────────────────────────────────────────────────────
// India-origin HR platform with ATS module; strong in mid-market APAC.
// Pattern: {tenant}.keka.com/careers

const KEKA_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'hr', 'blog', 'status', 'docs']);

export function extractKekaSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.keka.com')) return null;
  const s = url.hostname.replace('.keka.com', '').toLowerCase();
  return (!s || KEKA_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromKeka(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractKekaSlug(url);
  if (!slug) return { jobs: [], method: 'keka' };

  const base = `https://${slug}.keka.com`;

  interface KekaJob { id?: string | number; jobId?: string | number; title?: string; jobTitle?: string; location?: string | { name?: string; city?: string }; department?: string | { name?: string }; jobType?: string; employmentType?: string }
  interface KekaResponse { data?: KekaJob[]; jobs?: KekaJob[]; results?: KekaJob[]; openings?: KekaJob[] }

  const apiUrls = [
    `${base}/api/v1/careers/jobs?status=open`,
    `${base}/careers/api/v1/jobs`,
    `${base}/api/careers/jobs`,
    `${base}/careers/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<KekaResponse | KekaJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: KekaJob[] = Array.isArray(data)
      ? data
      : ((data as KekaResponse).data ?? (data as KekaResponse).jobs ?? (data as KekaResponse).results ?? (data as KekaResponse).openings ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.jobTitle ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.city) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = (j.id ?? j.jobId) ? String(j.id ?? j.jobId) : undefined;
      return [{ title, location, department, employmentType: j.jobType ?? j.employmentType, id, url: id ? `${base}/careers/job/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Keka API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'keka-api' }; }
  }
  return { jobs: [], method: 'keka' };
}
