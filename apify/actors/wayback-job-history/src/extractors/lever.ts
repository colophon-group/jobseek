import { log } from 'apify';
import { fetchArchivedJson } from '../fetch.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface LeverPosting {
  id: string;
  text: string;
  categories?: {
    location?: string;
    department?: string;
    team?: string;
    commitment?: string;
  };
  hostedUrl?: string;
}

/**
 * Detect Lever company slug from a URL.
 * Handles: jobs.lever.co/{company}
 */
export function extractLeverSlug(url: URL): string | null {
  if (url.hostname === 'jobs.lever.co' || url.hostname === 'jobs.eu.lever.co') {
    const slug = url.pathname.split('/').filter(Boolean)[0];
    return slug ?? null;
  }
  return null;
}

/**
 * Fetch jobs from the Lever public API via the Wayback Machine.
 */
export async function extractFromLever(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const slug = extractLeverSlug(url);
  if (!slug) return { jobs: [], method: 'lever-api' };

  // Try multiple Lever API endpoint forms — some are more commonly archived than others
  const apiUrls = [
    `https://api.lever.co/v0/postings/${slug}?mode=json`,
    `https://api.lever.co/v0/postings/${slug}`,
    `https://api.eu.lever.co/v0/postings/${slug}?mode=json`, // EU-hosted tenants
    `https://jobs.lever.co/${slug}?format=json`,
  ];

  let data: LeverPosting[] | null = null;
  for (const apiUrl of apiUrls) {
    log.debug(`Trying Lever API via Wayback: ${apiUrl}`);
    const raw = await fetchArchivedJson<LeverPosting[] | { data?: LeverPosting[] }>(timestamp, apiUrl);
    if (!raw) continue;
    const arr = Array.isArray(raw) ? raw : (raw as { data?: LeverPosting[] }).data ?? [];
    if (arr.length > 0) { data = arr; break; }
  }

  if (!data?.length) return { jobs: [], method: 'lever-api' };

  const jobs: JobPosting[] = data!.map(p => ({
    title: p.text,
    location: p.categories?.location,
    department: p.categories?.team ?? p.categories?.department,
    url: p.hostedUrl ?? `https://jobs.lever.co/${slug}/${p.id}`,
    id: p.id,
    employmentType: p.categories?.commitment,
  }));

  log.info(`Lever API: ${jobs.length} jobs via Wayback`);
  return { jobs, method: 'lever-api' };
}
