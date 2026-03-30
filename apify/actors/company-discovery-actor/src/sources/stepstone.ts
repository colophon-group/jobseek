import * as cheerio from 'cheerio';
import { fetchPage, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const DEFAULT_QUERIES = [
  'software', 'ingenieur', 'marketing', 'vertrieb', 'beratung',
  'finanzen', 'IT', 'projektmanagement', 'design', 'personalwesen',
  'logistik', 'pharma', 'automotive', 'versicherung', 'energie',
  'medien', 'bauingenieur', 'controlling', 'einkauf', 'qualitaet',
  // Swiss / Austrian market
  'banken', 'compliance', 'klinische forschung', 'medizintechnik',
  'uhrenindustrie', 'lebensmittel', 'treuhand',
];

export async function discoverFromStepstone(
  proxyUrl?: string,
  maxCompanies = 250,
  customQueries?: string[],
): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const seen = new Set<string>();
  const now = new Date().toISOString();
  const queries = customQueries?.length ? customQueries : DEFAULT_QUERIES;

  console.log(`StepStone: starting with ${queries.length} queries, max=${maxCompanies}`);

  for (const query of queries) {
    if (results.length >= maxCompanies) break;

    for (let page = 1; page <= 20; page++) {
      if (results.length >= maxCompanies) break;

      const url = `https://www.stepstone.de/jobs/${encodeURIComponent(query)}?page=${page}`;
      const html = await fetchPage({
        url,
        proxyUrl,
        headers: { 'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8' },
      });

      if (!html) break;

      const $ = cheerio.load(html);
      let found = 0;

      // Strategy 1: company name elements in job cards
      const companySelectors = [
        '[data-testid="company-name"]',
        '[class*="company-name"]',
        'a[href*="/cmp/"]',
        '[data-at="job-item-company-name"]',
        '.listing-company',
        'span[class*="Company"]',
      ];

      for (const sel of companySelectors) {
        if (found > 0) break;

        $(sel).each((_, el) => {
          const $el = $(el);
          const name = $el.text().trim().replace(/\s+/g, ' ');
          const href = $el.is('a') ? ($el.attr('href') || '') : ($el.closest('a[href*="/cmp/"]').attr('href') || '');

          if (!name || name.length < 2 || name.length > 120) return;
          const key = name.toLowerCase();
          if (seen.has(key)) return;
          seen.add(key);

          const boardUrl = href.startsWith('http') ? href
            : href ? `https://www.stepstone.de${href}`
            : `https://www.stepstone.de/cmp/${encodeURIComponent(name.toLowerCase().replace(/\s+/g, '-'))}.html`;

          results.push({
            company_name: name,
            job_board_url: boardUrl,
            estimated_jobs: 1,
            source: 'stepstone',
            discovered_at: now,
          });
          found++;
        });
      }

      // Strategy 2: JSON-LD structured data (common on job search pages)
      if (found === 0) {
        $('script[type="application/ld+json"]').each((_, el) => {
          try {
            const data = JSON.parse($(el).html() || '');
            const items = Array.isArray(data) ? data : [data];
            for (const item of items) {
              const org = item.hiringOrganization || item;
              if (org?.name) {
                const key = org.name.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                results.push({
                  company_name: org.name,
                  job_board_url: org.sameAs || org.url || `https://www.stepstone.de/cmp/${encodeURIComponent(org.name.toLowerCase().replace(/\s+/g, '-'))}.html`,
                  estimated_jobs: 1,
                  source: 'stepstone',
                  discovered_at: now,
                });
                found++;
              }
            }
          } catch { /* ignore */ }
        });
      }

      console.log(`StepStone: q="${query}" page=${page} +${found} (total=${results.length})`);
      if (found === 0) break;

      await sleep(1000 + Math.random() * 1500);
    }
  }

  console.log(`StepStone: finished with ${results.length} companies`);
  return results;
}
