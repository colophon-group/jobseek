import type { CheerioAPI } from 'cheerio';
import type { ExtractionResult, JobPosting } from '../types.js';

/**
 * Extract jobs from JSON-LD <script> blocks looking for @type: JobPosting items.
 */
export function extractFromJsonLd($: CheerioAPI): ExtractionResult {
  const jobs: JobPosting[] = [];

  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      const raw = $(el).html() ?? '';
      const data: unknown = JSON.parse(raw);

      // Handle both single object and array
      const items = Array.isArray(data) ? data : [data];

      for (const item of items) {
        if (!item || typeof item !== 'object') continue;
        const obj = item as Record<string, unknown>;

        // Direct JobPosting (or @type: ["JobPosting", "Thing"])
        if (isJobPosting(obj)) {
          const job = parseJobPosting(obj);
          if (job) jobs.push(job);
          continue;
        }

        // @graph array (WordPress / schema.org pattern)
        if (Array.isArray(obj['@graph'])) {
          for (const node of obj['@graph'] as unknown[]) {
            if (!node || typeof node !== 'object') continue;
            const n = node as Record<string, unknown>;
            if (isJobPosting(n)) {
              const job = parseJobPosting(n);
              if (job) jobs.push(job);
            }
          }
          continue;
        }

        // ItemList of JobPostings
        if ((obj['@type'] === 'ItemList' || (Array.isArray(obj['@type']) && (obj['@type'] as string[]).includes('ItemList'))) && Array.isArray(obj['itemListElement'])) {
          for (const listItem of obj['itemListElement'] as unknown[]) {
            if (!listItem || typeof listItem !== 'object') continue;
            const li = listItem as Record<string, unknown>;
            const inner = (li['item'] ?? li) as Record<string, unknown>;
            if (inner['@type'] === 'JobPosting') {
              const job = parseJobPosting(inner);
              if (job) jobs.push(job);
            }
          }
        }
      }
    } catch {
      // malformed JSON-LD — skip
    }
  });

  return { jobs, method: 'jsonld' };
}

function isJobPosting(obj: Record<string, unknown>): boolean {
  const t = obj['@type'];
  if (t === 'JobPosting') return true;
  if (Array.isArray(t)) return (t as unknown[]).includes('JobPosting');
  return false;
}

function parseJobPosting(obj: Record<string, unknown>): JobPosting | null {
  const title = String(obj['title'] ?? obj['name'] ?? '').trim();
  if (!title) return null;

  let location: string | undefined;
  const loc = obj['jobLocation'];
  // jobLocation can be a string, object, or array
  const locItems = Array.isArray(loc) ? loc : loc ? [loc] : [];
  const locParts: string[] = [];
  for (const l of locItems) {
    if (typeof l === 'string') {
      if (l) locParts.push(l);
    } else if (l && typeof l === 'object') {
      const locObj = l as Record<string, unknown>;
      const addr = locObj['address'] as Record<string, unknown> | undefined;
      const part = String(
        locObj['name'] ?? addr?.['addressLocality'] ?? addr?.['addressRegion'] ?? ''
      ).trim();
      if (part) locParts.push(part);
    }
  }
  // Also check jobLocationType for remote
  if (!locParts.length && obj['jobLocationType'] === 'TELECOMMUTE') {
    locParts.push('Remote');
  }
  location = locParts.length > 0 ? locParts.join(', ') : undefined;

  const identifier = obj['identifier'] as Record<string, unknown> | undefined;

  return {
    title,
    location,
    department: obj['occupationalCategory'] ? String(obj['occupationalCategory']) : undefined,
    url: obj['url'] ? String(obj['url']) : undefined,
    id: identifier?.['value'] ? String(identifier['value']) : undefined,
    employmentType: obj['employmentType'] ? String(obj['employmentType']) : undefined,
    validThrough: obj['validThrough'] ? String(obj['validThrough']).slice(0, 10) : undefined,
  };
}
