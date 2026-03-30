import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface PinpointJob {
  id?: string | number;
  title?: string;
  job_title?: string;
  location?: string | { name?: string; city?: string };
  department?: string | { name?: string };
  employment_type?: string;
  apply_url?: string;
  slug?: string;
}

interface PinpointResponse {
  jobs?: PinpointJob[];
  data?: PinpointJob[];
}

export function extractPinpointSlug(url: URL): string | null {
  if (url.hostname !== 'app.pinpointhq.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const seg = parts[0];
  if (!seg || seg === 'jobs' || seg === 'api' || seg.length < 2) return null;
  return seg.toLowerCase();
}

export async function extractFromPinpoint(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractPinpointSlug(url);
  if (!slug) return { jobs: [], method: 'pinpoint-api' };

  // Try multiple Pinpoint API patterns
  const apiUrls = [
    `https://app.pinpointhq.com/api/v1/job_applications/jobs?subdomain=${slug}`,
    `https://app.pinpointhq.com/api/v1/${slug}/jobs`,
    `https://${slug}.pinpointhq.com/api/v1/jobs`,
  ];

  let raw: PinpointJob[] = [];
  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<PinpointResponse | PinpointJob[]>(ts, apiUrl);
    if (!data) continue;
    raw = Array.isArray(data)
      ? data
      : ((data as PinpointResponse)?.jobs ?? (data as PinpointResponse)?.data ?? []);
    if (raw.length > 0) break;
  }

  if (!raw.length) return { jobs: [], method: 'pinpoint-api' };

  const jobs: JobPosting[] = raw.flatMap(j => {
    const title = j.title ?? j.job_title ?? '';
    if (!title) return [];
    const loc = j.location;
    let location: string | undefined;
    if (typeof loc === 'string') location = loc || undefined;
    else if (loc) location = loc.name ?? loc.city ?? undefined;
    const dept = typeof j.department === 'object' ? j.department?.name : j.department;
    const id = String(j.id ?? '');
    return [{ title, location, department: dept, employmentType: j.employment_type, id: id || undefined, url: j.apply_url || (j.slug ? `https://app.pinpointhq.com/${slug}/jobs/${j.slug}` : undefined) } as JobPosting];
  });

  log.info(`Pinpoint: ${jobs.length} jobs`);
  return { jobs, method: 'pinpoint-api' };
}
