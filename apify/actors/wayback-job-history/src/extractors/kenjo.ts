import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface KenjoJob {
  _id?: string;
  id?: string | number;
  name?: string;
  title?: string;
  jobTitle?: string;
  location?: string | { name?: string; city?: string; country?: string };
  department?: string | { name?: string };
  workSchedule?: string;
  employmentType?: string;
  remote?: boolean;
  status?: string;
  slug?: string;
}

interface KenjoResponse {
  data?: KenjoJob[];
  jobs?: KenjoJob[];
  positions?: KenjoJob[];
  offers?: KenjoJob[];
  items?: KenjoJob[];
}

/**
 * Detect Kenjo company slug from a URL.
 * Handles: app.kenjo.io/{company}/jobs
 */
export function extractKenjoSlug(url: URL): string | null {
  if (url.hostname !== 'app.kenjo.io') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const slug = parts[0];
  const reserved = new Set(['www', 'api', 'login', 'support', 'help', 'blog', 'admin', 'demo', 'jobs']);
  if (!slug || slug.length < 2 || reserved.has(slug.toLowerCase())) return null;
  return slug.toLowerCase();
}

/**
 * Fetch jobs from Kenjo ATS via the Wayback Machine.
 * Tries known API endpoint patterns for Kenjo's public job board.
 */
export async function extractFromKenjo(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractKenjoSlug(url);
  if (!slug) return { jobs: [], method: 'kenjo' };

  const apiUrls = [
    // Kenjo REST API patterns (tries most likely first)
    `https://app.kenjo.io/api/v1/public/companies/${slug}/job-offers`,
    `https://app.kenjo.io/api/v1/companies/${slug}/job-offers`,
    `https://app.kenjo.io/api/v2/public/companies/${slug}/job-offers`,
    `https://app.kenjo.io/${slug}/api/jobs`,
    `https://api.kenjo.io/v1/job-board/${slug}/jobs`,
  ];

  for (const apiUrl of apiUrls) {
    log.debug(`Trying Kenjo API via Wayback: ${apiUrl}`);
    const data = await fetchArchivedJson<KenjoResponse | KenjoJob[]>(ts, apiUrl);
    if (!data) continue;

    const rawJobs: KenjoJob[] = Array.isArray(data)
      ? data
      : (
          (data as KenjoResponse).data ??
          (data as KenjoResponse).jobs ??
          (data as KenjoResponse).positions ??
          (data as KenjoResponse).offers ??
          (data as KenjoResponse).items ??
          []
        );

    if (rawJobs.length > 0) {
      const jobs: JobPosting[] = rawJobs.flatMap(j => {
        const title = j.name ?? j.title ?? j.jobTitle ?? '';
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
        const department = typeof dept === 'string'
          ? dept || undefined
          : dept && typeof dept === 'object' ? dept.name || undefined : undefined;

        const id = j._id ?? (j.id ? String(j.id) : undefined);
        return [{
          title,
          location,
          department,
          employmentType: j.workSchedule ?? j.employmentType,
          id,
          url: id ? `https://app.kenjo.io/${slug}/jobs/${id}` : undefined,
        } as JobPosting];
      });

      if (jobs.length > 0) {
        log.info(`Kenjo API: ${jobs.length} jobs via Wayback`);
        return { jobs, method: 'kenjo-api' };
      }
    }
  }

  return { jobs: [], method: 'kenjo' };
}
