import * as cheerio from 'cheerio';
import { fetchPage, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const DEFAULT_QUERIES = [
  'software engineer', 'data scientist', 'product manager', 'designer',
  'marketing manager', 'sales representative', 'devops engineer',
  'project manager', 'business analyst', 'HR manager',
  'financial analyst', 'operations manager', 'machine learning',
  'frontend developer', 'backend developer', 'security engineer',
  'cloud architect', 'QA engineer', 'content strategist',
  'customer success', 'supply chain', 'mechanical engineer',
  'nurse', 'accountant', 'teacher',
  // Swiss / EU market specific
  'wealth management', 'private banking', 'pharma regulatory',
  'clinical research', 'biotech scientist', 'insurance actuary',
  'watchmaking', 'precision engineering', 'medtech',
];

const LOCATIONS = [
  'United States', 'Germany', 'United Kingdom', 'France', 'Netherlands',
  'Switzerland', 'Austria', 'Sweden', 'Denmark', 'Finland',
  'Spain', 'Italy', 'Belgium', 'Poland', 'Portugal',
];

export async function discoverFromLinkedIn(
  proxyUrl?: string,
  maxCompanies = 400,
  customQueries?: string[],
): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const seen = new Set<string>();
  const jobCounts = new Map<string, number>();
  const companyUrls = new Map<string, string>();
  const now = new Date().toISOString();
  const queries = customQueries?.length ? customQueries : DEFAULT_QUERIES;

  console.log(`LinkedIn: starting with ${queries.length} queries x ${LOCATIONS.length} locations, max=${maxCompanies}`);

  for (const query of queries) {
    if (results.length >= maxCompanies) break;

    for (const location of LOCATIONS) {
      if (results.length >= maxCompanies) break;

      for (let start = 0; start < 200; start += 25) {
        if (results.length >= maxCompanies) break;

        // LinkedIn's public guest API for job search
        const url = `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=${encodeURIComponent(query)}&location=${encodeURIComponent(location)}&start=${start}`;

        const html = await fetchPage({ url, proxyUrl });
        if (!html) break;

        const $ = cheerio.load(html);
        const cards = $('li');
        if (cards.length === 0) break;

        let newFound = 0;
        cards.each((_, el) => {
          const $card = $(el);

          // Company name - multiple selector strategies
          let companyName = '';
          let companyUrl = '';

          // Strategy 1: subtitle with link
          const subtitleLink = $card.find('h4.base-search-card__subtitle a, .base-search-card__subtitle a').first();
          if (subtitleLink.length) {
            companyName = subtitleLink.text().trim();
            companyUrl = subtitleLink.attr('href') || '';
          }

          // Strategy 2: any h4 in the card
          if (!companyName) {
            const h4 = $card.find('h4 a').first();
            companyName = h4.text().trim();
            companyUrl = h4.attr('href') || '';
          }

          // Strategy 3: subtitle text without link
          if (!companyName) {
            companyName = $card.find('h4, [class*="subtitle"]').first().text().trim();
          }

          if (!companyName || companyName.length < 2 || companyName.length > 120) return;

          const key = companyName.toLowerCase();
          jobCounts.set(key, (jobCounts.get(key) || 0) + 1);
          if (companyUrl) companyUrls.set(key, companyUrl);

          if (!seen.has(key)) {
            seen.add(key);
            results.push({
              company_name: companyName,
              job_board_url: companyUrl || `https://www.linkedin.com/company/${encodeURIComponent(companyName.toLowerCase().replace(/[^a-z0-9]+/g, '-'))}/jobs/`,
              estimated_jobs: 1,
              source: 'linkedin',
              discovered_at: now,
            });
            newFound++;
          }
        });

        console.log(`LinkedIn: q="${query}" loc="${location}" start=${start} +${newFound} (total=${results.length})`);
        if (newFound === 0 && cards.length < 10) break;

        await sleep(600 + Math.random() * 1200);
      }
    }
  }

  // Update estimated_jobs based on how many listings we saw per company
  for (const company of results) {
    const key = company.company_name.toLowerCase();
    company.estimated_jobs = jobCounts.get(key) || 1;
    const url = companyUrls.get(key);
    if (url) company.job_board_url = url;
  }

  console.log(`LinkedIn: finished with ${results.length} companies`);
  return results;
}
