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
