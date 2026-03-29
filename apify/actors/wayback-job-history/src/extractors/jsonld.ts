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

        // Direct JobPosting
        if (obj['@type'] === 'JobPosting') {
          const job = parseJobPosting(obj);
          if (job) jobs.push(job);
          continue;
        }

        // ItemList of JobPostings
        if (obj['@type'] === 'ItemList' && Array.isArray(obj['itemListElement'])) {
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

function parseJobPosting(obj: Record<string, unknown>): JobPosting | null {
  const title = String(obj['title'] ?? obj['name'] ?? '').trim();
  if (!title) return null;

  let location: string | undefined;
  const loc = obj['jobLocation'];
  if (typeof loc === 'string') {
    location = loc;
  } else if (loc && typeof loc === 'object') {
    const locObj = loc as Record<string, unknown>;
    const addr = locObj['address'] as Record<string, unknown> | undefined;
    location = String(
      locObj['name'] ?? addr?.['addressLocality'] ?? addr?.['addressRegion'] ?? ''
    ).trim() || undefined;
  }

  const identifier = obj['identifier'] as Record<string, unknown> | undefined;

  return {
    title,
    location,
    department: obj['occupationalCategory'] ? String(obj['occupationalCategory']) : undefined,
    url: obj['url'] ? String(obj['url']) : undefined,
    id: identifier?.['value'] ? String(identifier['value']) : undefined,
    employmentType: obj['employmentType'] ? String(obj['employmentType']) : undefined,
  };
}
