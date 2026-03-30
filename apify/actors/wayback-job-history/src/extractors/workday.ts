import { log } from 'apify';
import { fetchArchivedJson, fetchArchivedPage } from '../fetch.js';
import { findJobsInObject } from './nextdata.js';
import type { ExtractionResult, JobPosting } from '../types.js';

interface WorkdayJob {
  title?: string;
  externalPath?: string;
  locationsText?: string;
  jobFunctionSummary?: string;
  timeType?: string;
  id?: string;
}

interface WorkdayApiResponse {
  jobPostings?: WorkdayJob[];
  total?: number;
}

/**
 * Detect Workday tenant and board from a URL.
 * Handles:
 *   https://{tenant}.wd{n}.myworkdayjobs.com/{board}
 *   https://{tenant}.wd{n}.myworkdayjobs.com/{board}/jobs
 */
export function extractWorkdayParams(url: URL): { tenant: string; instance: string; board: string } | null {
  const match = url.hostname.match(/^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$/i);
  if (!match) return null;

  const tenant   = match[1];
  const instance = match[2].toLowerCase();

  const parts = url.pathname.split('/').filter(Boolean);
  const board = parts[0] ?? tenant;

  return { tenant, instance, board };
}

/**
 * Fetch jobs from the Workday CXS API via the Wayback Machine.
 * The Workday API is a POST endpoint, so Wayback is unlikely to have it.
 * This tries the GET variant and falls back to HTML parsing.
 */
export async function extractFromWorkday(
  url: URL,
  timestamp: string,
): Promise<ExtractionResult> {
  const params = extractWorkdayParams(url);
  if (!params) return { jobs: [], method: 'workday' };

  const { tenant, instance, board } = params;

  // Try the Workday CXS jobs API (some older responses archived as GET)
  const apiUrl =
    `https://${tenant}.${instance}.myworkdayjobs.com/wday/cxs/${tenant}/${board}/jobs`;

  log.debug(`Trying Workday API via Wayback: ${apiUrl}`);
  const data = await fetchArchivedJson<WorkdayApiResponse>(timestamp, apiUrl);

  if (data?.jobPostings && data.jobPostings.length > 0) {
    const jobs: JobPosting[] = data.jobPostings.map(j => ({
      title: j.title ?? '',
      location: j.locationsText,
      department: j.jobFunctionSummary,
      url: j.externalPath
        ? `https://${tenant}.${instance}.myworkdayjobs.com${j.externalPath}`
        : undefined,
      id: j.id,
      employmentType: j.timeType,
    })).filter(j => j.title);

    if (jobs.length > 0) {
      log.info(`Workday CXS API: ${jobs.length} jobs`);
      return { jobs, method: 'workday-api' };
    }
  }

  // Fall back to HTML parsing — Workday pages embed job data in window.__appParams__
  // or a script tag with the serialized store
  const html = await fetchArchivedPage(timestamp, url.toString());
  if (!html) return { jobs: [], method: 'workday' };

  // Try multiple Workday HTML embedded JSON patterns
  const htmlPatterns: RegExp[] = [
    /window\.__WD_APP_CONFIG__\s*=\s*(\{[\s\S]*?\});\s*(?:window\.|<\/script>)/,
    /window\.__wd_store__\s*=\s*(\{[\s\S]*?\});/,
    /window\.WORKDAY_STORE\s*=\s*(\{[\s\S]*?\});/,
  ];

  for (const pat of htmlPatterns) {
    const m = html.match(pat);
    if (!m) continue;
    try {
      const result = findJobsInObject(JSON.parse(m[1]), 'workday-html');
      if (result.jobs.length > 0) return result;
    } catch { /* ignore */ }
  }

  // application/json script blocks (Workday sometimes embeds job data here)
  for (const m of html.matchAll(/<script[^>]*type="application\/json"[^>]*>([\s\S]*?)<\/script>/g)) {
    try {
      const result = findJobsInObject(JSON.parse(m[1]), 'workday-html-json');
      if (result.jobs.length > 0) return result;
    } catch { /* ignore */ }
  }

  // Generic __NEXT_DATA__ / embedded JSON walk
  const nextMatch = html.match(/<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
  if (nextMatch) {
    try {
      const result = findJobsInObject(JSON.parse(nextMatch[1]), 'workday-nextdata');
      if (result.jobs.length > 0) return result;
    } catch { /* ignore */ }
  }

  return { jobs: [], method: 'workday-none' };
}
