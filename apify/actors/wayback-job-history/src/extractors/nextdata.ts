import type { CheerioAPI } from 'cheerio';
import type { ExtractionResult, JobPosting } from '../types.js';

/**
 * Extract jobs from Next.js __NEXT_DATA__ script tag.
 * Recursively walks the serialized page props looking for arrays of job objects.
 */
export function extractFromNextData($: CheerioAPI): ExtractionResult {
  const scriptContent = $('#__NEXT_DATA__').html();
  if (!scriptContent) return { jobs: [], method: 'nextdata' };

  try {
    const data: unknown = JSON.parse(scriptContent);
    return findJobsInObject(data, 'nextdata');
  } catch {
    return { jobs: [], method: 'nextdata' };
  }
}

/**
 * Walk any object/array tree looking for arrays whose elements resemble job postings.
 */
export function findJobsInObject(root: unknown, method: string): ExtractionResult {
  const candidates: JobPosting[] = [];
  const visited = new WeakSet<object>();

  function walk(node: unknown, depth: number): void {
    if (depth > 12) return;
    if (!node || typeof node !== 'object') return;

    const obj = node as object;
    if (visited.has(obj)) return;
    visited.add(obj);

    if (Array.isArray(node)) {
      // Check if this looks like a job array
      const jobLike = node.filter(looksLikeJob);
      if (jobLike.length > 0 && jobLike.length === node.length) {
        for (const item of jobLike) {
          candidates.push(normalizeJob(item as Record<string, unknown>));
        }
        return; // don't recurse into items we already consumed
      }
      for (const item of node) walk(item, depth + 1);
    } else {
      for (const val of Object.values(node as Record<string, unknown>)) {
        walk(val, depth + 1);
      }
    }
  }

  walk(root, 0);

  // Deduplicate by title
  const seen = new Set<string>();
  const jobs = candidates.filter(j => {
    if (!j.title || seen.has(j.title.toLowerCase())) return false;
    seen.add(j.title.toLowerCase());
    return true;
  });

  return { jobs, method };
}

function looksLikeJob(item: unknown): boolean {
  if (!item || typeof item !== 'object') return false;
  const obj = item as Record<string, unknown>;

  const hasTitle =
    typeof obj['title'] === 'string' ||
    typeof obj['jobTitle'] === 'string' ||
    typeof obj['position'] === 'string' ||
    typeof obj['name'] === 'string';

  if (!hasTitle) return false;

  const hasJobField =
    'location' in obj ||
    'department' in obj ||
    'requisitionId' in obj ||
    'applyUrl' in obj ||
    'hostedUrl' in obj ||
    'employmentType' in obj ||
    'jobCategory' in obj ||
    'categories' in obj ||
    'team' in obj;

  return hasJobField;
}

export function normalizeJob(obj: Record<string, unknown>): JobPosting {
  const title = String(
    obj['title'] ?? obj['jobTitle'] ?? obj['position'] ?? obj['name'] ?? ''
  ).trim();

  let location: string | undefined;
  const rawLoc = obj['location'] ?? obj['locationName'] ?? obj['city'];
  if (typeof rawLoc === 'string') {
    location = rawLoc.trim() || undefined;
  } else if (rawLoc && typeof rawLoc === 'object') {
    const locObj = rawLoc as Record<string, unknown>;
    location = String(locObj['name'] ?? locObj['city'] ?? locObj['text'] ?? '').trim() || undefined;
  }

  let department: string | undefined;
  const rawDept = obj['department'] ?? obj['team'] ?? obj['categories'] ?? obj['jobCategory'];
  if (typeof rawDept === 'string') {
    department = rawDept.trim() || undefined;
  } else if (rawDept && typeof rawDept === 'object') {
    const deptObj = rawDept as Record<string, unknown>;
    department = String(deptObj['name'] ?? deptObj['team'] ?? '').trim() || undefined;
  }

  return {
    title,
    location,
    department,
    url: obj['url'] ? String(obj['url']) :
         obj['hostedUrl'] ? String(obj['hostedUrl']) :
         obj['applyUrl'] ? String(obj['applyUrl']) : undefined,
    id: obj['id'] ? String(obj['id']) :
        obj['requisitionId'] ? String(obj['requisitionId']) :
        obj['jobId'] ? String(obj['jobId']) : undefined,
    employmentType: obj['employmentType'] ? String(obj['employmentType']) :
                    obj['workType'] ? String(obj['workType']) : undefined,
  };
}
