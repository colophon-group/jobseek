import * as cheerio from 'cheerio';
import { fetchPage, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const DEFAULT_QUERIES = [
  'softwareentwickler', 'ingenieur', 'marketing manager', 'vertrieb',
  'consultant', 'controller', 'projektleiter', 'designer',
  'recruiter', 'buchhalter', 'data scientist', 'devops',
  'produktmanager', 'einkäufer', 'qualitätsmanager',
];

export async function discoverFromXing(
  proxyUrl?: string,
  maxCompanies = 200,
  customQueries?: string[],
): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const seen = new Set<string>();
  const now = new Date().toISOString();
  const queries = customQueries?.length ? customQueries : DEFAULT_QUERIES;

  console.log(`Xing: starting with ${queries.length} queries, max=${maxCompanies}`);

  for (const query of queries) {
    if (results.length >= maxCompanies) break;

    for (let page = 1; page <= 20; page++) {
      if (results.length >= maxCompanies) break;

      const url = `https://www.xing.com/jobs/search?keywords=${encodeURIComponent(query)}&page=${page}`;
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
        'a[href*="/pages/"]',
        'a[href*="/companies/"]',
        '[class*="Company"]',
        'p[class*="company"]',
      ];

      for (const sel of companySelectors) {
        if (found > 0) break;

        $(sel).each((_, el) => {
          const $el = $(el);
          const name = $el.text().trim().replace(/\s+/g, ' ');
          const href = $el.is('a') ? ($el.attr('href') || '') : ($el.find('a').attr('href') || '');

          if (!name || name.length < 2 || name.length > 120) return;
          const key = name.toLowerCase();
          if (seen.has(key)) return;
          seen.add(key);

          const boardUrl = href.startsWith('http') ? href
            : href ? `https://www.xing.com${href}`
            : `https://www.xing.com/pages/${encodeURIComponent(name.toLowerCase().replace(/\s+/g, '-'))}`;

          results.push({
            company_name: name,
            job_board_url: boardUrl,
            estimated_jobs: 1,
            source: 'xing',
            discovered_at: now,
          });
          found++;
        });
      }

      // Strategy 2: JSON-LD structured data
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
                  job_board_url: org.sameAs || org.url || `https://www.xing.com/pages/${encodeURIComponent(org.name.toLowerCase().replace(/\s+/g, '-'))}`,
                  estimated_jobs: 1,
                  source: 'xing',
                  discovered_at: now,
                });
                found++;
              }
            }
          } catch { /* ignore */ }
        });
      }

      // Strategy 3: Parse __NEXT_DATA__ or similar hydration JSON
      if (found === 0) {
        $('script#__NEXT_DATA__, script[id*="state"]').each((_, el) => {
          try {
            const text = $(el).html() || '';
            // Look for company names in JSON blob
            const companyMatches = text.matchAll(/"company(?:Name|_name)"\s*:\s*"([^"]{2,80})"/g);
            for (const match of companyMatches) {
              const name = match[1];
              const key = name.toLowerCase();
              if (seen.has(key)) continue;
              seen.add(key);
              results.push({
                company_name: name,
                job_board_url: `https://www.xing.com/pages/${encodeURIComponent(name.toLowerCase().replace(/\s+/g, '-'))}`,
                estimated_jobs: 1,
                source: 'xing',
                discovered_at: now,
              });
              found++;
            }
          } catch { /* ignore */ }
        });
      }

      console.log(`Xing: q="${query}" page=${page} +${found} (total=${results.length})`);
      if (found === 0) break;

      await sleep(1000 + Math.random() * 1500);
    }
  }

  console.log(`Xing: finished with ${results.length} companies`);
  return results;
}
