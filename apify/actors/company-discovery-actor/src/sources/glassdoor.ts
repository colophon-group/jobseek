import * as cheerio from 'cheerio';
import { fetchPage, sleep } from '../http.js';
import { fetchPageWithPuppeteer } from '../browser.js';
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

    // Glassdoor renders content via JavaScript — try HTTP first (faster), fall back to Puppeteer.
    let html = await fetchPage({
      url,
      proxyUrl,
      headers: { 'Accept-Language': 'en-US,en;q=0.9' },
    });

    // If HTTP returned no employer content, render with a real browser.
    if (!html || !hasEmployerContent(html)) {
      console.log(`Glassdoor: page=${page} HTTP gave no content, retrying with Puppeteer…`);
      html = await fetchPageWithPuppeteer(url, {
        waitMs: 2500,
        extraHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
      });
      if (!html) {
        // Try alternative URL pattern before giving up on this page
        const altUrl = `https://www.glassdoor.com/Reviews/company-reviews.htm?page=${page}`;
        html = await fetchPageWithPuppeteer(altUrl, { waitMs: 2500 });
        if (!html) break;
      }
    }

    const found = parseGlassdoorPage(cheerio.load(html), results, seen, now);
    console.log(`Glassdoor: page=${page} +${found} (total=${results.length})`);

    if (found === 0) break;
    await sleep(1500 + Math.random() * 2000);
  }

  console.log(`Glassdoor: finished with ${results.length} companies`);
  return results;
}

/** Quick check: does the HTML look like a rendered employer listing? */
function hasEmployerContent(html: string): boolean {
  return /Overview|employer|company/i.test(html) && html.length > 5000;
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
