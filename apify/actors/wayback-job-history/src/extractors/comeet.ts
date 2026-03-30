import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface ComeetJob {
  uid?: string;
  id?: string | number;
  name?: string;
  title?: string;
  location?: string | { name?: string; city?: string; country?: string };
  department?: string | { name?: string };
  employment_type?: string;
  url?: string;
}

interface ComeetResponse {
  positions?: ComeetJob[];
  jobs?: ComeetJob[];
}

export function extractComeetSlug(url: URL): string | null {
  if (url.hostname !== 'recruiting.comeet.co') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  if (parts[0] !== 'jobs' || !parts[1] || parts[1].length < 2) return null;
  return parts[1];
}

export async function extractFromComeet(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractComeetSlug(url);
  if (!slug) return { jobs: [], method: 'comeet-api' };

  // Try multiple Comeet API patterns
  const apiUrls = [
    `https://www.comeet.co/jobs/${slug}/ALL/positions`,
    `https://recruiting.comeet.co/api/v2.0/companies/${slug}/positions`,
    `https://api.comeet.co/companies/${slug}/positions`,
  ];

  let raw: ComeetJob[] = [];
  for (const apiUrl of apiUrls) {
    const data = await fetchArchivedJson<ComeetResponse | ComeetJob[]>(ts, apiUrl);
    if (!data) continue;
    raw = Array.isArray(data)
      ? data
      : ((data as ComeetResponse)?.positions ?? (data as ComeetResponse)?.jobs ?? []);
    if (raw.length > 0) break;
  }

  if (!raw.length) return { jobs: [], method: 'comeet-api' };

  const jobs: JobPosting[] = raw.flatMap(j => {
    const title = j.name ?? j.title ?? '';
    if (!title) return [];
    const loc = j.location;
    let location: string | undefined;
    if (typeof loc === 'string') location = loc || undefined;
    else if (loc) location = [loc.city, loc.country].filter(Boolean).join(', ') || loc.name || undefined;
    const dept = typeof j.department === 'object' ? j.department?.name : j.department;
    const id = j.uid ?? String(j.id ?? '');
    return [{ title, location, department: dept, employmentType: j.employment_type, id: id || undefined, url: j.url || (id ? `https://recruiting.comeet.co/jobs/${slug}/${id}` : undefined) } as JobPosting];
  });

  log.info(`Comeet: ${jobs.length} jobs`);
  return { jobs, method: 'comeet-api' };
}
