import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface RipplingJob {
  id?: string;
  title?: string;
  name?: string;
  location?: string | { city?: string; state?: string; country?: string; remote?: boolean };
  department?: string | { name?: string };
  employmentType?: string;
  remoteStatus?: string;
  status?: string;
}

interface RipplingResponse {
  results?: RipplingJob[];
  jobs?: RipplingJob[];
  data?: RipplingJob[];
}

/**
 * Detect Rippling company slug from a URL.
 * Handles: ats.rippling.com/{company_slug}
 */
export function extractRipplingSlug(url: URL): string | null {
  if (url.hostname !== 'ats.rippling.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const slug = parts[0];
  if (!slug || slug.length < 2 || slug === 'api' || /^\d+$/.test(slug)) return null;
  return slug.toLowerCase();
}

/**
 * Fetch jobs from Rippling ATS via the Wayback Machine.
 * Tries the known API endpoint patterns.
 */
export async function extractFromRippling(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractRipplingSlug(url);
  if (!slug) return { jobs: [], method: 'rippling' };

  const apiUrls = [
    `https://ats.rippling.com/api/v1/${slug}/jobs`,
    `https://ats.rippling.com/${slug}/jobs.json`,
  ];

  for (const apiUrl of apiUrls) {
    log.debug(`Trying Rippling API via Wayback: ${apiUrl}`);
    const data = await fetchArchivedJson<RipplingResponse | RipplingJob[]>(timestamp, apiUrl);
    if (!data) continue;

    const rawJobs: RipplingJob[] = Array.isArray(data)
      ? data
      : ((data as RipplingResponse).results ?? (data as RipplingResponse).jobs ?? (data as RipplingResponse).data ?? []);

    if (rawJobs.length > 0) {
      const jobs: JobPosting[] = rawJobs.flatMap(j => {
        const title = j.title ?? j.name ?? '';
        if (!title) return [];

        const loc = j.location;
        let location: string | undefined;
        if (j.remoteStatus === 'REMOTE' || j.remoteStatus === 'remote') {
          location = 'Remote';
        } else if (typeof loc === 'string') {
          location = loc || undefined;
        } else if (loc && typeof loc === 'object') {
          if (loc.remote) {
            location = 'Remote';
          } else {
            location = [loc.city, loc.state, loc.country].filter(Boolean).join(', ') || undefined;
          }
        }

        const dept = j.department;
        const department = typeof dept === 'string' ? dept || undefined
          : dept && typeof dept === 'object' ? dept.name || undefined
          : undefined;

        return [{
          title,
          location,
          department,
          employmentType: j.employmentType,
          id: j.id ? String(j.id) : undefined,
          url: j.id ? `https://ats.rippling.com/${slug}/jobs/${j.id}` : undefined,
        }];
      });

      if (jobs.length > 0) {
        log.info(`Rippling API: ${jobs.length} jobs via Wayback`);
        return { jobs, method: 'rippling-api' };
      }
    }
  }

  return { jobs: [], method: 'rippling' };
}
