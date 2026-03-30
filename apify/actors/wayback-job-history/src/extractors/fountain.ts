import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface FountainLocation {
  name?: string;
  address?: string;
  city?: string;
  state?: string;
  country?: string;
}

interface FountainFunnel {
  id?: string;
  title?: string;
  name?: string;
  location?: FountainLocation | string;
  department?: string;
  job_type?: string;
  employment_type?: string;
  remote?: boolean;
  status?: string;
}

interface FountainApiResponse {
  funnels?: FountainFunnel[];
  data?: FountainFunnel[];
  positions?: FountainFunnel[];
}

/**
 * Detect Fountain company slug from a URL.
 * Handles: jobs.fountain.com/{company_slug}
 */
export function extractFountainSlug(url: URL): string | null {
  if (url.hostname !== 'jobs.fountain.com') return null;
  const parts = url.pathname.split('/').filter(Boolean);
  const slug = parts[0];
  if (!slug || slug.length < 2 || slug === 'api' || slug === 'apply') return null;
  // Skip UUID-style segments (individual job applications)
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(slug)) return null;
  return slug.toLowerCase();
}

/**
 * Fetch jobs from Fountain via the Wayback Machine.
 * Tries known API endpoints; Fountain's job boards are Next.js SPAs with
 * embedded data — the generic extractors handle HTML fallback.
 */
export async function extractFromFountain(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractFountainSlug(url);
  if (!slug) return { jobs: [], method: 'fountain' };

  // Try Fountain's public positions API
  const apiUrls = [
    `https://jobs.fountain.com/api/v2/brands/${slug}/funnels?status=open`,
    `https://jobs.fountain.com/api/v2/brands/${slug}/funnels`,
    `https://jobs.fountain.com/${slug}/positions.json`,
  ];

  for (const apiUrl of apiUrls) {
    log.debug(`Trying Fountain API via Wayback: ${apiUrl}`);
    const data = await fetchArchivedJson<FountainApiResponse>(timestamp, apiUrl);
    const rawJobs: FountainFunnel[] = data?.funnels ?? data?.data ?? data?.positions ?? [];

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
          location = [loc.city, loc.state, loc.country].filter(Boolean).join(', ') || loc.address || loc.name || undefined;
        }
        return [{
          title,
          location,
          department: j.department,
          employmentType: j.job_type ?? j.employment_type,
          id: j.id,
          url: j.id ? `https://jobs.fountain.com/${slug}/${j.id}` : undefined,
        }];
      });

      if (jobs.length > 0) {
        log.info(`Fountain API: ${jobs.length} jobs via Wayback`);
        return { jobs, method: 'fountain-api' };
      }
    }
  }

  return { jobs: [], method: 'fountain' };
}
