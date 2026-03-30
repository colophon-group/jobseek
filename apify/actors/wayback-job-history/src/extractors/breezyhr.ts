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
  if (!url.hostname.endsWith('.breezy.hr')) return null;
  const s = url.hostname.replace('.breezy.hr', '');
  return s && s !== 'www' && s.length >= 2 ? s : null;
}

export async function extractFromBreezyHR(url: URL, ts: string): Promise<ExtractionResult> {
  const slug = extractBreezySlug(url);
  if (!slug) return { jobs: [], method: 'breezyhr-api' };

  // Breezy public API: GET https://{slug}.breezy.hr/json
  const data = await fetchArchivedJson<BreezyJob[]>(ts, `https://${slug}.breezy.hr/json`);
  if (!Array.isArray(data) || !data.length) return { jobs: [], method: 'breezyhr-api' };

  const jobs: JobPosting[] = data.map(j => {
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
