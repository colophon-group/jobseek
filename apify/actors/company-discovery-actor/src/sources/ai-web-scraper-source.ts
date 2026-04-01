/**
 * Company discovery via the Apify `apify/ai-web-scraper` actor.
 *
 * Calls the public Apify store actor which uses AI (Claude/GPT) to extract structured
 * data from any web page — ideal for JS-heavy job directories we can't parse with
 * plain selectors. The actor runs inside the same Apify account, so APIFY_TOKEN is
 * automatically available when running on the platform.
 *
 * Targeted pages: job/startup directories with dynamic content not covered by our
 * static scrapers (Built In, Wellfound new-format pages, etc.).
 */
import { Actor, log } from 'apify';
import type { CompanyDiscovery } from '../types.js';

interface TargetPage {
  url: string;
  instructions: string;
}

// Pages where AI extraction shines — JS-rendered directories with unstructured layouts.
const TARGET_PAGES: TargetPage[] = [
  {
    url: 'https://builtin.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string, the URL to their careers/jobs page or their Builtin profile). Return a JSON array.',
  },
  {
    url: 'https://www.builtinnyc.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string, their jobs/careers page). Return a JSON array.',
  },
  {
    url: 'https://www.builtinla.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string). Return a JSON array.',
  },
  {
    url: 'https://www.builtinchicago.org/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string). Return a JSON array.',
  },
  {
    url: 'https://www.builtinaustin.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string). Return a JSON array.',
  },
  {
    url: 'https://www.builtinseattle.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string). Return a JSON array.',
  },
  {
    url: 'https://www.builtinboston.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string). Return a JSON array.',
  },
  {
    url: 'https://www.builtincolorado.com/companies',
    instructions:
      'Find every company card on this page. For each company extract: company_name (string) and job_board_url (string). Return a JSON array.',
  },
];

interface ExtractedItem {
  company_name?: string;
  name?: string;
  job_board_url?: string;
  careers_url?: string;
  url?: string;
  website?: string;
}

/**
 * Discover companies by calling the `apify/ai-web-scraper` actor on each target page.
 * Requires APIFY_TOKEN to be available (auto-injected on the Apify platform).
 */
export async function discoverFromAiWebScraper(maxCompanies = 500): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const seen = new Set<string>();
  const now = new Date().toISOString();

  for (const target of TARGET_PAGES) {
    if (results.length >= maxCompanies) break;
    log.info(`AI Web Scraper: processing ${target.url}`);

    try {
      const run = await Actor.call('apify/ai-web-scraper', {
        startUrls: [{ url: target.url }],
        // The actor accepts either `instructions` or `pageInstructions` depending on version.
        instructions: target.instructions,
        pageInstructions: target.instructions,
        outputSchema: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              company_name: { type: 'string', description: 'Company or organisation name' },
              job_board_url: { type: 'string', description: 'URL to the company job listings or careers page' },
            },
            required: ['company_name'],
          },
        },
        // Keep the sub-run cheap: one page, short timeout.
        maxPagesPerCrawl: 1,
        maxCrawlingDepth: 0,
      });

      if (!run?.defaultDatasetId) {
        log.warning(`AI Web Scraper: run for ${target.url} returned no dataset`);
        continue;
      }

      const dataset = await Actor.openDataset(run.defaultDatasetId);
      const { items } = await dataset.getData({ limit: maxCompanies - results.length });

      let pageFound = 0;
      for (const raw of items as ExtractedItem[]) {
        const name = (raw.company_name || raw.name || '').trim();
        const boardUrl = (raw.job_board_url || raw.careers_url || raw.url || raw.website || '').trim();
        if (!name || name.length < 2 || name.length > 120) continue;

        const key = name.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);

        results.push({
          company_name: name,
          job_board_url: boardUrl || `https://www.google.com/search?q=${encodeURIComponent(name + ' careers')}`,
          estimated_jobs: 1,
          source: 'ai-web-scraper',
          discovered_at: now,
        });
        pageFound++;
      }

      log.info(`AI Web Scraper: ${pageFound} companies from ${target.url} (total=${results.length})`);
    } catch (err) {
      log.error(`AI Web Scraper: failed for ${target.url}: ${err}`);
    }
  }

  log.info(`AI Web Scraper: finished with ${results.length} total companies`);
  return results;
}
