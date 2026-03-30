import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface SoftgardenJob {
  jobId?: string | number;
  id?: string | number;
  jobTitle?: string;
  title?: string;
  name?: string;
  jobCategory?: { name?: string } | string;
  location?: string | { city?: string; country?: string; name?: string };
  employmentType?: string;
  applyOnlineUrl?: string;
}

interface SoftgardenResponse {
  joblistings?: SoftgardenJob[];
  jobs?: SoftgardenJob[];
  data?: SoftgardenJob[];
}

export function extractSoftgardenSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.softgarden.io') && !url.hostname.endsWith('.softgarden.de')) return null;
  const s = url.hostname.split('.')[0].toLowerCase();
  return s && s !== 'www' && s.length >= 2 ? s : null;
}

export async function extractFromSoftgarden(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractSoftgardenSlug(url);
  if (!slug) return { jobs: [], method: 'softgarden-api' };

  const tld = url.hostname.endsWith('.softgarden.de') ? 'softgarden.de' : 'softgarden.io';

  // Try JSON API endpoints
  const endpoints = [
    `https://${slug}.${tld}/api/v2/jobs`,
    `https://${slug}.${tld}/job/list`,
  ];

  for (const endpoint of endpoints) {
    const data = await fetchArchivedJson<SoftgardenResponse | SoftgardenJob[]>(ts, endpoint);
    if (!data) continue;

    const raw: SoftgardenJob[] = Array.isArray(data)
      ? data
      : ((data as SoftgardenResponse).joblistings ?? (data as SoftgardenResponse).jobs ?? (data as SoftgardenResponse).data ?? []);

    if (!raw.length) continue;

    const jobs: JobPosting[] = raw.flatMap(j => {
      const title = j.jobTitle ?? j.title ?? j.name ?? '';
      if (!title) return [];
      const loc = j.location;
      let location: string | undefined;
      if (typeof loc === 'string') location = loc || undefined;
      else if (loc) location = [loc.city, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
      const dept = typeof j.jobCategory === 'object' ? j.jobCategory?.name : j.jobCategory;
      const id = String(j.jobId ?? j.id ?? '');
      return [{ title, location, department: dept, employmentType: j.employmentType, id: id || undefined, url: j.applyOnlineUrl || (id ? `https://${slug}.${tld}/job/${id}` : undefined) } as JobPosting];
    });

    log.info(`Softgarden: ${jobs.length} jobs`);
    return { jobs, method: 'softgarden-api' };
  }

  return { jobs: [], method: 'softgarden-api' };
}

// ── Jobteaser ─────────────────────────────────────────────────────────────────

export function extractJobteaserSlug(url: URL): string | null {
  if (url.hostname !== 'jobteaser.com' && url.hostname !== 'www.jobteaser.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const companyIdx = parts.indexOf('company');
  if (companyIdx === -1) return null;
  const seg = parts[companyIdx + 1];
  const reserved = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog', 'admin', 'careers', 'demo', 'about', 'en', 'de', 'fr', 'es', 'it', 'nl', 'pt']);
  if (!seg || reserved.has(seg.toLowerCase()) || seg.length < 2) return null;
  if (/^\d+$/.test(seg)) return null;
  return seg.toLowerCase();
}

export async function extractFromJobteaser(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractJobteaserSlug(url);
  if (!slug) return { jobs: [], method: 'jobteaser' };

  interface JobteaserJob {
    reference?: string;
    name?: string;
    title?: string;
    office?: { city?: string; country?: string | { name?: string } };
    department?: { name?: string } | string;
    contract_type?: string | { name?: string };
    remote?: boolean;
  }
  interface JobteaserResponse { job_offers?: JobteaserJob[]; jobs?: JobteaserJob[]; data?: JobteaserJob[] }

  const apiUrls = [
    `https://www.jobteaser.com/api/v1/job-offers?company_slug=${slug}&per_page=100&page=1&locale=en`,
    `https://www.jobteaser.com/en/api/v1/job-offers?company_slug=${slug}&per_page=100`,
    `https://www.jobteaser.com/api/v1/companies/${slug}/job-offers?per_page=100`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<JobteaserResponse | JobteaserJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: JobteaserJob[] = Array.isArray(data)
      ? data
      : ((data as JobteaserResponse).job_offers ?? (data as JobteaserResponse).jobs ?? (data as JobteaserResponse).data ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.name ?? j.title ?? '';
      if (!title) return [];
      const office = j.office;
      let location: string | undefined;
      if (j.remote) {
        location = 'Remote';
      } else if (office) {
        const country = typeof office.country === 'object' ? office.country?.name : office.country;
        location = [office.city, country].filter(Boolean).join(', ') || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const ct = j.contract_type;
      const employmentType = typeof ct === 'string' ? ct || undefined : ct?.name || undefined;
      const id = j.reference;
      return [{ title, location, department, employmentType, id, url: id ? `https://www.jobteaser.com/en/company/${slug}/jobs/${id}` : undefined }];
    });
    if (jobs.length > 0) { log.info(`Jobteaser API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'jobteaser-api' }; }
  }
  return { jobs: [], method: 'jobteaser' };
}

// ── Welcome to the Jungle (WTTJ) ──────────────────────────────────────────────

export function extractWttjSlug(url: URL): string | null {
  if (url.hostname !== 'welcometothejungle.com' && url.hostname !== 'www.welcometothejungle.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const companyIdx = parts.indexOf('companies');
  if (companyIdx === -1) return null;
  const seg = parts[companyIdx + 1];
  const reserved = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'about', 'press', 'en', 'fr', 'de', 'es', 'pt', 'companies']);
  if (!seg || reserved.has(seg.toLowerCase()) || seg.length < 2) return null;
  if (/^\d+$/.test(seg)) return null;
  return seg.toLowerCase();
}

export async function extractFromWttj(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractWttjSlug(url);
  if (!slug) return { jobs: [], method: 'wttj' };

  interface WttjJob {
    slug?: string;
    name?: string;
    title?: string;
    office?: { city?: string; country?: { name?: string } | string };
    department?: { name?: string } | string;
    contract_type?: { name?: string } | string;
    remote?: string | boolean;
  }
  interface WttjResponse { jobs?: WttjJob[]; data?: WttjJob[]; results?: WttjJob[] }

  const apiUrls = [
    `https://api.welcometothejungle.com/api/v1/organizations/${slug}/jobs?page=1&per_page=100`,
    `https://www.welcometothejungle.com/api/v1/organizations/${slug}/jobs?page=1&per_page=100`,
    `https://api.welcometothejungle.com/api/v1/organizations/${slug}/job-offers?per_page=100`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<WttjResponse | WttjJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: WttjJob[] = Array.isArray(data)
      ? data
      : ((data as WttjResponse).jobs ?? (data as WttjResponse).data ?? (data as WttjResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.name ?? j.title ?? '';
      if (!title) return [];
      const office = j.office;
      let location: string | undefined;
      const isRemote = j.remote === true || j.remote === 'full';
      if (isRemote) {
        location = 'Remote';
      } else if (office) {
        const country = typeof office.country === 'object' ? office.country?.name : office.country;
        location = [office.city, country].filter(Boolean).join(', ') || undefined;
      }
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const ct = j.contract_type;
      const employmentType = typeof ct === 'string' ? ct || undefined : ct?.name || undefined;
      const id = j.slug;
      return [{ title, location, department, employmentType, id, url: id ? `https://www.welcometothejungle.com/en/companies/${slug}/jobs/${id}` : undefined }];
    });
    if (jobs.length > 0) { log.info(`WTTJ API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'wttj-api' }; }
  }
  return { jobs: [], method: 'wttj' };
}

// ── TalentLyft ─────────────────────────────────────────────────────────────────
// EU ATS (Croatia-origin): {company}.talentlyft.com/jobs

const TALENTLYFT_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'about']);

export function extractTalentLyftSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.talentlyft.com')) return null;
  const s = url.hostname.replace('.talentlyft.com', '').toLowerCase();
  return (!s || TALENTLYFT_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromTalentLyft(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractTalentLyftSlug(url);
  if (!slug) return { jobs: [], method: 'talentlyft' };

  interface TlJob { id?: string | number; title?: string; name?: string; location?: string | { name?: string; city?: string }; department?: string | { name?: string }; jobType?: string; employment_type?: string }
  interface TlResponse { jobs?: TlJob[]; data?: TlJob[]; results?: TlJob[] }

  const apiUrls = [
    `https://${slug}.talentlyft.com/api/v1/jobs`,
    `https://${slug}.talentlyft.com/jobs.json`,
    `https://${slug}.talentlyft.com/api/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<TlResponse | TlJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: TlJob[] = Array.isArray(data)
      ? data
      : ((data as TlResponse).jobs ?? (data as TlResponse).data ?? (data as TlResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.city) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department, employmentType: j.jobType ?? j.employment_type, id, url: id ? `https://${slug}.talentlyft.com/jobs/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`TalentLyft API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'talentlyft-api' }; }
  }
  return { jobs: [], method: 'talentlyft' };
}

// ── EasyCruit ─────────────────────────────────────────────────────────────────
// Scandinavian ATS dominant in Norway, Sweden, Denmark — public sector + enterprises.
// URL pattern: {company}.easycruit.com/vacancy/{vacancy_id}/{sub_id}

const EASYCRUIT_RESERVED = new Set(['www', 'app', 'api', 'admin', 'support', 'help', 'blog', 'demo', 'test', 'staging', 'dev', 'static', 'assets', 'mail']);

export function extractEasyCruitSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.easycruit.com')) return null;
  const s = url.hostname.replace('.easycruit.com', '').toLowerCase();
  return (!s || EASYCRUIT_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromEasyCruit(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractEasyCruitSlug(url);
  if (!slug) return { jobs: [], method: 'easycruit' };

  interface EasyCruitVacancy {
    id?: string | number;
    vacancy_id?: string | number;
    title?: string;
    name?: string;
    position?: string;
    location?: string | { name?: string; city?: string; country?: string };
    department?: string | { name?: string };
    employment_type?: string | { name?: string };
    contract_type?: string;
  }
  interface EasyCruitResponse {
    vacancies?: EasyCruitVacancy[];
    jobs?: EasyCruitVacancy[];
    data?: EasyCruitVacancy[];
    results?: EasyCruitVacancy[];
  }

  const apiUrls = [
    `https://${slug}.easycruit.com/api/v2/vacancies`,
    `https://${slug}.easycruit.com/api/v1/vacancies`,
    `https://${slug}.easycruit.com/recruitment/jobs.json`,
    `https://${slug}.easycruit.com/api/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<EasyCruitResponse | EasyCruitVacancy[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: EasyCruitVacancy[] = Array.isArray(data)
      ? data
      : ((data as EasyCruitResponse).vacancies ?? (data as EasyCruitResponse).jobs ?? (data as EasyCruitResponse).data ?? (data as EasyCruitResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? j.position ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.city) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const et = j.employment_type;
      const employmentType = typeof et === 'string' ? et || undefined : et?.name || j.contract_type || undefined;
      const id = j.id ? String(j.id) : (j.vacancy_id ? String(j.vacancy_id) : undefined);
      return [{ title, location, department, employmentType, id, url: id ? `https://${slug}.easycruit.com/vacancy/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`EasyCruit API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'easycruit-api' }; }
  }
  return { jobs: [], method: 'easycruit' };
}

// ── Varbi ─────────────────────────────────────────────────────────────────────
// Swedish/Scandinavian ATS — municipalities, counties, universities, enterprises.
// URL pattern: {company}.varbi.com/{locale}/what:job/jobID:{id}/

const VARBI_RESERVED = new Set(['www', 'app', 'api', 'www2', 'static', 'assets', 'blog', 'support', 'help', 'admin', 'demo', 'test', 'staging', 'dev', 'mail', 'smtp']);

export function extractVarbiSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.varbi.com')) return null;
  const s = url.hostname.replace('.varbi.com', '').toLowerCase();
  return (!s || VARBI_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromVarbi(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractVarbiSlug(url);
  if (!slug) return { jobs: [], method: 'varbi' };

  interface VarbiJob {
    id?: string | number;
    jobID?: string | number;
    title?: string;
    name?: string;
    heading?: string;
    location?: string | { name?: string; city?: string };
    department?: string | { name?: string };
    employment_type?: string;
    employmentType?: string;
  }
  interface VarbiResponse {
    jobs?: VarbiJob[];
    data?: VarbiJob[];
    results?: VarbiJob[];
    positions?: VarbiJob[];
  }

  const apiUrls = [
    `https://${slug}.varbi.com/api/v1/jobs`,
    `https://${slug}.varbi.com/api/jobs`,
    `https://${slug}.varbi.com/what:jobs.json`,
    `https://${slug}.varbi.com/en/what:jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<VarbiResponse | VarbiJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: VarbiJob[] = Array.isArray(data)
      ? data
      : ((data as VarbiResponse).jobs ?? (data as VarbiResponse).data ?? (data as VarbiResponse).results ?? (data as VarbiResponse).positions ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? j.heading ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.city) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = j.id ? String(j.id) : (j.jobID ? String(j.jobID) : undefined);
      const employmentType = j.employment_type ?? j.employmentType;
      return [{ title, location, department, employmentType, id, url: id ? `https://${slug}.varbi.com/en/what:job/jobID:${id}/` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Varbi API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'varbi-api' }; }
  }
  return { jobs: [], method: 'varbi' };
}

// ── Occupop ─────────────────────────────────────────────────────────────────────
// Irish/UK enterprise ATS: {company}.occupop.com/jobs

const OCCUPOP_RESERVED = new Set(['www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'admin', 'careers', 'demo', 'about', 'pricing']);

export function extractOccupopSlug(url: URL): string | null {
  if (!url.hostname.endsWith('.occupop.com')) return null;
  const s = url.hostname.replace('.occupop.com', '').toLowerCase();
  return (!s || OCCUPOP_RESERVED.has(s) || s.length < 2) ? null : s;
}

export async function extractFromOccupop(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractOccupopSlug(url);
  if (!slug) return { jobs: [], method: 'occupop' };

  interface OccupopJob { id?: string | number; title?: string; name?: string; location?: string | { name?: string; city?: string }; department?: string | { name?: string }; jobType?: string; contractType?: string }
  interface OccupopResponse { jobs?: OccupopJob[]; data?: OccupopJob[]; results?: OccupopJob[] }

  const apiUrls = [
    `https://${slug}.occupop.com/api/v1/jobs`,
    `https://${slug}.occupop.com/jobs.json`,
    `https://${slug}.occupop.com/api/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<OccupopResponse | OccupopJob[]>(ts, apiUrl);
    if (!data) continue;
    const rawJobs: OccupopJob[] = Array.isArray(data)
      ? data
      : ((data as OccupopResponse).jobs ?? (data as OccupopResponse).data ?? (data as OccupopResponse).results ?? []);
    if (!rawJobs.length) continue;
    const jobs: JobPosting[] = rawJobs.flatMap(j => {
      const title = j.title ?? j.name ?? '';
      if (!title) return [];
      const loc = j.location;
      const location = typeof loc === 'string' ? loc || undefined : (loc?.name ?? loc?.city) || undefined;
      const dept = j.department;
      const department = typeof dept === 'string' ? dept || undefined : dept?.name || undefined;
      const id = j.id ? String(j.id) : undefined;
      return [{ title, location, department, employmentType: j.jobType ?? j.contractType, id, url: id ? `https://${slug}.occupop.com/jobs/${id}` : undefined } as JobPosting];
    });
    if (jobs.length > 0) { log.info(`Occupop API: ${jobs.length} jobs via Wayback`); return { jobs, method: 'occupop-api' }; }
  }
  return { jobs: [], method: 'occupop' };
}
