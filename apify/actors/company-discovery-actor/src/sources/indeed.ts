import * as cheerio from 'cheerio';
import { fetchPage, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const DEFAULT_QUERIES = [
  'software', 'engineering', 'healthcare', 'finance', 'retail',
  'technology', 'consulting', 'manufacturing', 'education', 'marketing',
  'data science', 'sales', 'design', 'logistics', 'pharmaceutical',
  'automotive', 'insurance', 'telecommunications', 'energy', 'media',
  'construction', 'banking', 'hospitality', 'legal', 'real estate',
];

export async function discoverFromIndeed(
  proxyUrl?: string,
  maxCompanies = 400,
  customQueries?: string[],
): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const seen = new Set<string>();
  const now = new Date().toISOString();
  const queries = customQueries?.length ? customQueries : DEFAULT_QUERIES;

  console.log(`Indeed: starting with ${queries.length} queries, max=${maxCompanies}`);

  for (const query of queries) {
    if (results.length >= maxCompanies) break;

    for (let start = 0; start < 100; start += 20) {
      if (results.length >= maxCompanies) break;

      const url = `https://www.indeed.com/companies?q=${encodeURIComponent(query)}&l=&start=${start}`;
      const html = await fetchPage({ url, proxyUrl });
      if (!html) break;

      const $ = cheerio.load(html);
      let found = 0;

      // Primary: links to /cmp/ company profile pages
      $('a[href*="/cmp/"]').each((_, el) => {
        const $a = $(el);
        const href = $a.attr('href') || '';

        // Skip sub-pages like /reviews, /faq, /salaries, /jobs (we want the profile link)
        if (/\/(reviews|faq|salaries|jobs|about|photos)\b/.test(href)) return;

        const name = $a.text().trim();
        if (!name || name.length < 2 || name.length > 120) return;

        const key = name.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);

        // Look for job count in surrounding card
        const card = $a.closest('[class*="card"], [class*="result"], [class*="Company"], li, article');
        const cardText = card.length ? card.text() : '';
        const jobMatch = cardText.match(/(\d[\d,]*)\s*(?:jobs?|positions?|openings?|open\s*roles?)/i);
        const estimatedJobs = jobMatch ? parseInt(jobMatch[1].replace(/,/g, ''), 10) : 1;

        results.push({
          company_name: name,
          job_board_url: href.startsWith('http') ? href : `https://www.indeed.com${href}`,
          estimated_jobs: estimatedJobs,
          source: 'indeed',
          discovered_at: now,
        });
        found++;
      });

      // Fallback: look for structured data or JSON-LD
      if (found === 0) {
        $('script[type="application/ld+json"]').each((_, el) => {
          try {
            const data = JSON.parse($(el).html() || '');
            const items = Array.isArray(data) ? data : [data];
            for (const item of items) {
              if (item['@type'] === 'Organization' && item.name) {
                const key = item.name.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                results.push({
                  company_name: item.name,
                  job_board_url: item.url || `https://www.indeed.com/cmp/${encodeURIComponent(item.name)}`,
                  estimated_jobs: 1,
                  source: 'indeed',
                  discovered_at: now,
                });
                found++;
              }
            }
          } catch { /* ignore parse errors */ }
        });
      }

      console.log(`Indeed: q="${query}" start=${start} +${found} (total=${results.length})`);
      if (found === 0) break;

      await sleep(800 + Math.random() * 1500);
    }
  }

  console.log(`Indeed: finished with ${results.length} companies`);
  return results;
}
