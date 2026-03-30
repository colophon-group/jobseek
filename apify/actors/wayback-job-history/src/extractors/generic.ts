import type { CheerioAPI } from 'cheerio';
import type { ExtractionResult, JobPosting } from '../types.js';

/**
 * Generic CSS-based extractor for server-rendered career pages.
 * Tries common job card selectors used across many ATS / career sites.
 */
export function extractGeneric($: CheerioAPI, _url: URL): ExtractionResult {
  // Try each selector pattern; stop at first that produces results
  const selectorGroups = [
    // Data attribute patterns — most reliable
    ['[data-job-id]', '[data-requisition-id]', '[data-automation="job-result-item"]'],
    ['[data-ui="job-item"]', '[data-testid="job-item"]', '[data-testid*="job-card"]'],
    ['[data-controller="job-card"]', '[data-controller="position"]'],
    // Class fragment patterns (sorted by specificity)
    ['[class*="JobCard"]', '[class*="job-card"]', '[class*="job_card"]'],
    ['[class*="job-row"]', '[class*="JobRow"]', '[class*="job_row"]'],
    ['[class*="job-listing"]', '[class*="JobListing"]'],
    ['[class*="position-item"]', '[class*="PositionItem"]', '[class*="positionCard"]'],
    ['[class*="opening-item"]', '[class*="OpeningItem"]', '[class*="opening-row"]'],
    // Semantic / role-based
    ['article[class*="job"]', 'article[class*="position"]', 'article[class*="opening"]'],
    ['[role="listitem"][class*="job"]', 'li[class*="job"]', 'li[class*="position"]'],
    // Generic container guesses
    ['.opening', '.position', '.vacancy', '.role'],
    ['tr[class*="job"]', 'tr[class*="position"]'],
  ];

  for (const selectors of selectorGroups) {
    const jobs = trySelectors($, selectors);
    if (jobs.length > 0) return { jobs, method: 'generic-css' };
  }

  // Last resort: look for any <a> that points to a /jobs/ path with a title
  const linkJobs = extractFromJobLinks($);
  if (linkJobs.length > 0) return { jobs: linkJobs, method: 'job-links' };

  return { jobs: [], method: 'none' };
}

function trySelectors($: CheerioAPI, selectors: string[]): JobPosting[] {
  const jobs: JobPosting[] = [];
  const seen = new Set<string>();

  for (const selector of selectors) {
    $(selector).each((_, el) => {
      const $el = $(el);

      // Extract title — try various patterns in priority order
      let title = '';

      const titleEl = $el
        .find('h1, h2, h3, h4, h5')
        .filter((_, h) => $(h).text().trim().length > 0)
        .first();
      if (titleEl.length) title = titleEl.text().trim();

      if (!title) {
        const titleAttrEl = $el
          .find('[class*="title"], [class*="Title"], [class*="name"], [class*="Name"]')
          .first();
        if (titleAttrEl.length) title = titleAttrEl.text().trim();
      }

      if (!title) {
        // Fall back to the element's own text or first link text
        title = $el.find('a').first().text().trim();
      }

      if (!title || title.length > 200 || title.length < 2) return;

      const key = title.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);

      const locationEl = $el
        .find('[class*="location"], [class*="Location"], [class*="city"], [data-location]')
        .first();
      const deptEl = $el
        .find('[class*="department"], [class*="Department"], [class*="team"], [class*="Team"], [class*="category"]')
        .first();
      const linkEl = $el.find('a[href]').first();

      const id =
        $el.attr('data-job-id') ??
        $el.attr('data-id') ??
        $el.attr('data-requisition-id') ??
        undefined;

      jobs.push({
        title,
        location: locationEl.text().trim() || undefined,
        department: deptEl.text().trim() || undefined,
        url: linkEl.attr('href') || undefined,
        id,
      });
    });

    if (jobs.length > 0) break;
  }

  return jobs;
}

/**
 * Fallback: find <a href="/jobs/..."> or <a href="...careers..."> links and infer titles.
 */
function extractFromJobLinks($: CheerioAPI): JobPosting[] {
  const jobs: JobPosting[] = [];
  const seen = new Set<string>();

  $('a[href]').each((_, el) => {
    const $a = $(el);
    const href = $a.attr('href') ?? '';
    const title = $a.text().trim();

    if (!title || title.length < 3 || title.length > 150) return;

    // Href should look job-related
    if (
      !/\/(jobs?|careers?|positions?|openings?|apply|posting|vacancy|vacancies|stellenangebote?|offres?-d-emploi|requisition)\//i.test(href) &&
      !/[?&](jid|job_id|jobId|req_id|reqId|jobReqId)=/i.test(href)
    ) return;

    const key = title.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);

    jobs.push({ title, url: href });
  });

  return jobs;
}
