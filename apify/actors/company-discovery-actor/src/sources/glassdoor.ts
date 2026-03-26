import * as cheerio from 'cheerio';
import { fetchPage, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

export async function discoverFromGlassdoor(
  proxyUrl?: string,
  maxCompanies = 300,
): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const seen = new Set<string>();
  const now = new Date().toISOString();

  console.log(`Glassdoor: starting browse, max=${maxCompanies}`);

  // Glassdoor employer browse - paginated directory
  for (let page = 1; page <= 80; page++) {
    if (results.length >= maxCompanies) break;

    const url = `https://www.glassdoor.com/Explore/browse-companies.htm?overall_rating_low=0&page=${page}`;
    const html = await fetchPage({
      url,
      proxyUrl,
      headers: { 'Accept-Language': 'en-US,en;q=0.9' },
    });

    if (!html) {
      // Try alternative URL pattern
      const altUrl = `https://www.glassdoor.com/Reviews/company-reviews.htm?page=${page}`;
      const altHtml = await fetchPage({ url: altUrl, proxyUrl });
      if (!altHtml) break;
      parseGlassdoorPage(cheerio.load(altHtml), results, seen, now);
      await sleep(1500 + Math.random() * 2000);
      continue;
    }

    const found = parseGlassdoorPage(cheerio.load(html), results, seen, now);
    console.log(`Glassdoor: page=${page} +${found} (total=${results.length})`);

    if (found === 0) break;
    await sleep(1500 + Math.random() * 2000);
  }

  console.log(`Glassdoor: finished with ${results.length} companies`);
  return results;
}

function parseGlassdoorPage(
  $: cheerio.CheerioAPI,
  results: CompanyDiscovery[],
  seen: Set<string>,
  now: string,
): number {
  let found = 0;

  // Strategy 1: Overview links (employer profile pages)
  $('a[href*="/Overview/"]').each((_, el) => {
    const $a = $(el);
    const name = $a.text().trim().replace(/\s+/g, ' ');
    const href = $a.attr('href') || '';

    if (!name || name.length < 2 || name.length > 120) return;
    // Skip navigation/breadcrumb links
    if (name.toLowerCase().includes('browse') || name.toLowerCase().includes('explore')) return;

    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);

    const card = $a.closest('[class*="card"], [class*="employer"], [class*="cell"], li, tr, article');
    const text = card.length ? card.text() : '';
    const jobMatch = text.match(/(\d[\d,]*)\s*(?:jobs?|openings?|positions?)/i);
    const estimatedJobs = jobMatch ? parseInt(jobMatch[1].replace(/,/g, ''), 10) : 1;

    results.push({
      company_name: name,
      job_board_url: href.startsWith('http') ? href : `https://www.glassdoor.com${href}`,
      estimated_jobs: estimatedJobs,
      source: 'glassdoor',
      discovered_at: now,
    });
    found++;
  });

  // Strategy 2: Reviews links as fallback
  if (found === 0) {
    $('a[href*="/Reviews/"][href*="-Reviews-"]').each((_, el) => {
      const $a = $(el);
      const name = $a.text().trim().replace(/\s+/g, ' ');
      const href = $a.attr('href') || '';

      if (!name || name.length < 2 || name.length > 120) return;
      const key = name.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);

      results.push({
        company_name: name,
        job_board_url: href.startsWith('http') ? href : `https://www.glassdoor.com${href}`,
        estimated_jobs: 1,
        source: 'glassdoor',
        discovered_at: now,
      });
      found++;
    });
  }

  // Strategy 3: JSON-LD structured data
  if (found === 0) {
    $('script[type="application/ld+json"]').each((_, el) => {
      try {
        const data = JSON.parse($(el).html() || '');
        const items = Array.isArray(data) ? data : data.itemListElement || [data];
        for (const item of items) {
          const org = item.item || item;
          if (org['@type'] === 'Organization' && org.name) {
            const key = org.name.toLowerCase();
            if (seen.has(key)) continue;
            seen.add(key);
            results.push({
              company_name: org.name,
              job_board_url: org.url || `https://www.glassdoor.com`,
              estimated_jobs: 1,
              source: 'glassdoor',
              discovered_at: now,
            });
            found++;
          }
        }
      } catch { /* ignore */ }
    });
  }

  return found;
}
