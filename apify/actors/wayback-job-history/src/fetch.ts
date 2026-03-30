import { log } from 'apify';
import { gotScraping } from 'got-scraping';
const WB = 'https://web.archive.org/web';
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

export async function fetchArchivedPage(ts: string, url: string): Promise<string | null> {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(`${WB}/${ts}id_/${url}`, { signal: AbortSignal.timeout(30_000), headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept': 'text/html,application/xhtml+xml,application/json,*/*', 'Accept-Language': 'en-US,en;q=0.9' }, redirect: 'follow' });
      if (res.status === 429) { await sleep(15_000 * (i + 1)); continue; }
      if (res.status === 404) return null;
      if (!res.ok) { await sleep(3_000 * (i + 1)); continue; }
      return await res.text();
    } catch { await sleep(3_000 * (i + 1)); }
  }
  return null;
}

export async function fetchArchivedJson<T>(ts: string, url: string): Promise<T | null> {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(`${WB}/${ts}id_/${url}`, { signal: AbortSignal.timeout(20_000), headers: { 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0 (compatible; WaybackJobHistory/1.0)' } });
      if (res.status === 429) { await sleep(12_000 * (i + 1)); continue; }
      if (res.status === 404) return null;
      if (!res.ok) { await sleep(2_000 * (i + 1)); continue; }
      const t = await res.text();
      return t.trimStart().startsWith('<') ? null : JSON.parse(t) as T;
    } catch { await sleep(2_000 * (i + 1)); }
  }
  return null;
}

// ── hiring.cafe live engagement signal ───────────────────────────────────────
export interface HiringCafeSignal { found: boolean; activeListings: number; avgViews: number; avgApplications: number; lowEngagement: boolean; signal: string | null }

const HC_API = 'https://hiring.cafe/api/search-jobs';
const HC_COMPANIES_API = 'https://hiring.cafe/api/search-companies';
const HC_BODY = (q: string) => JSON.stringify({ searchQuery: q, filters: [], page: 0, pageSize: 20, sortBy: 'date' });
const HC_COMPANY_BODY = (q: string) => JSON.stringify({ searchQuery: q, filters: [], page: 0, pageSize: 10 });
const HC_HEADERS = { 'Content-Type': 'application/json', 'Accept': 'application/json', 'Origin': 'https://hiring.cafe', 'Referer': 'https://hiring.cafe/' };

/** Extract the most distinctive token from a company name for fuzzy matching against hiring.cafe results. */
function companyMatchToken(name: string): string {
  const cleaned = name.toLowerCase()
    .replace(/\.(com|io|co|ai|net|org|app|dev|us|eu|de|fr|uk)\b/g, ' $1') // split "monday.com" → "monday com"
    .replace(/[.,&()'"/]/g, ' ')    // strip punctuation to spaces
    .replace(/\b(inc|llc|ltd|corp|ag|gmbh|sa|nv|plc|the|group|holdings?|technologies|solutions|services|systems|software|consulting)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (!cleaned) return name.toLowerCase().slice(0, 12);

  // For single-word result, return it (up to 14 chars)
  const words = cleaned.split(' ').filter(w => w.length >= 2);
  if (!words.length) return cleaned.slice(0, 12);
  if (words.length === 1) return words[0].slice(0, 14);

  // Prefer the longest distinctive word — e.g. "Bank of America" → "america", "Deutsche Bank" → "deutsche"
  const TLD_WORDS = new Set(['com', 'io', 'co', 'ai', 'net', 'org', 'app', 'dev', 'us', 'eu', 'de', 'fr', 'uk']);
  const GENERIC = new Set(['bank', 'capital', 'media', 'global', 'digital', 'cloud', 'data', 'labs', 'ventures', 'partners', 'studio', 'agency', 'team', ...TLD_WORDS]);
  const ranked = [...words].sort((a, b) => b.length - a.length);
  const distinctive = ranked.find(w => !GENERIC.has(w)) ?? ranked[0];

  // For 2-word companies: if second word is a TLD, just use the first
  if (words.length === 2 && TLD_WORDS.has(words[1])) return words[0].slice(0, 14);
  // Otherwise concatenate both words (most specific token)
  if (words.length === 2) return words.join('').slice(0, 14);

  return distinctive.slice(0, 14);
}

function parseHCResponse(text: string, company: string): HiringCafeSignal | null {
  if (text.includes('cf-browser-verification') || text.includes('Just a moment') || !text.trimStart().startsWith('{')) return null;
  type HCJob = { viewCount?: number; applicationCount?: number; v5_processed_company_data?: { name?: string } };
  const token = companyMatchToken(company);
  const jobs = ((JSON.parse(text) as { results?: HCJob[] }).results ?? []).filter(j => (j.v5_processed_company_data?.name ?? '').toLowerCase().includes(token));
  if (!jobs.length) return { found: false, activeListings: 0, avgViews: 0, avgApplications: 0, lowEngagement: false, signal: `${company} not found on hiring.cafe` };
  const avgV = jobs.reduce((s, j) => s + (j.viewCount ?? 0), 0) / jobs.length;
  const avgA = jobs.reduce((s, j) => s + (j.applicationCount ?? 0), 0) / jobs.length;
  const allZeroApps = jobs.length >= 3 && jobs.every(j => (j.applicationCount ?? 0) === 0);
  const low = (avgV < 5 && avgA === 0) || allZeroApps;
  return { found: true, activeListings: jobs.length, avgViews: avgV, avgApplications: avgA, lowEngagement: low, signal: low ? `hiring.cafe:${jobs.length}j avg${avgV.toFixed(1)}v ${avgA.toFixed(0)}a—low${allZeroApps ? '(all-zero-apps)' : ''}` : avgA > 10 ? `hiring.cafe:${jobs.length}j avg${avgA.toFixed(0)}apps—genuine` : null };
}

/** In-process cache to avoid hitting hiring.cafe multiple times for the same company in batch runs. */
const _hcCache = new Map<string, HiringCafeSignal | null>();

export async function checkHiringCafeSignal(company: string): Promise<HiringCafeSignal | null> {
  const cacheKey = companyMatchToken(company);
  if (_hcCache.has(cacheKey)) {
    log.debug(`HC signal cache hit for ${company}`);
    return _hcCache.get(cacheKey)!;
  }
  const result = await _checkHiringCafeSignalUncached(company);
  _hcCache.set(cacheKey, result);
  return result;
}

async function _checkHiringCafeSignalUncached(company: string): Promise<HiringCafeSignal | null> {
  // Strategy 1: native fetch (fast, sometimes blocked by Cloudflare)
  try {
    const res = await fetch(HC_API, { method: 'POST', signal: AbortSignal.timeout(12_000), headers: { ...HC_HEADERS, 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36' }, body: HC_BODY(company) });
    if (res.ok && res.status !== 403 && res.status !== 429) {
      const text = await res.text();
      const result = parseHCResponse(text, company);
      if (result !== null) return result;
      // If CF blocked, fall through to gotScraping
    }
  } catch (e) { log.debug(`HC signal fetch: ${e}`); }

  // Strategy 2: gotScraping with browser fingerprint spoofing (better CF bypass)
  try {
    const r = await gotScraping({ url: HC_API, method: 'POST', headers: HC_HEADERS, headerGeneratorOptions: { browsers: ['chrome'], operatingSystems: ['macos'], locales: ['en-US'] }, body: HC_BODY(company), timeout: { request: 15_000 } });
    if (r.statusCode === 200) {
      const result = parseHCResponse(r.body, company);
      if (result !== null) { log.debug(`HC signal via gotScraping for ${company}`); return result; }
    }
  } catch (e) { log.debug(`HC signal gotScraping: ${e}`); }

  // Strategy 2.5: playwright-extra + stealth plugin — real browser with anti-fingerprinting patches
  // Only used if strategies 1 and 2 are blocked (Cloudflare). Heavier but much harder to detect.
  try {
    const token = companyMatchToken(company);
    const { chromium: chromiumExtra } = await import('playwright-extra');
    const { default: StealthPlugin } = await import('puppeteer-extra-plugin-stealth');
    chromiumExtra.use(StealthPlugin());
    const browser = await chromiumExtra.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'] });
    try {
      const ctx = await browser.newContext({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36', locale: 'en-US', timezoneId: 'America/New_York' });
      const page = await ctx.newPage();
      // Navigate to hiring.cafe to establish session/cookies, then call API from browser context
      await page.goto('https://hiring.cafe', { waitUntil: 'domcontentloaded', timeout: 30_000 });
      await page.waitForTimeout(2000);
      type HCJob = { viewCount?: number; applicationCount?: number; v5_processed_company_data?: { name?: string } };
      type HCCo = { name?: string; totalActiveListings?: number; activeListings?: number };
      const [jobsRes, cosRes] = await Promise.all([
        page.evaluate(async (body: string) => {
          try { const r = await fetch('/api/search-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body }); return r.ok ? r.text() : null; } catch { return null; }
        }, HC_BODY(company)),
        page.evaluate(async (body: string) => {
          try { const r = await fetch('/api/search-companies', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body }); return r.ok ? r.text() : null; } catch { return null; }
        }, HC_COMPANY_BODY(company)),
      ]);
      await ctx.close();
      // Parse companies response first (more targeted)
      if (cosRes && cosRes.trimStart().startsWith('{')) {
        const companies: HCCo[] = (JSON.parse(cosRes) as { results?: HCCo[] }).results ?? [];
        const matched = companies.filter(co => (co.name ?? '').toLowerCase().includes(token));
        if (matched.length > 0) {
          const total = matched.reduce((s, co) => s + (co.totalActiveListings ?? co.activeListings ?? 0), 0);
          log.debug(`HC signal via stealth-playwright (companies) for ${company}: ${total} listings`);
          return { found: true, activeListings: total, avgViews: 0, avgApplications: 0, lowEngagement: total === 0, signal: total === 0 ? `hiring.cafe(stealth-co):found,0listings—low` : total > 20 ? `hiring.cafe(stealth-co):${total}listings—genuine` : null };
        }
      }
      if (jobsRes && jobsRes.trimStart().startsWith('{')) {
        const result = parseHCResponse(jobsRes, company);
        if (result !== null) { log.debug(`HC signal via stealth-playwright (jobs) for ${company}`); return result; }
      }
    } finally { await browser.close(); }
  } catch (e) { log.debug(`HC signal stealth-playwright: ${e}`); }

  // Strategy 2b: search-companies endpoint — returns company records directly (more targeted than search-jobs)
  try {
    const r = await gotScraping({ url: HC_COMPANIES_API, method: 'POST', headers: HC_HEADERS, headerGeneratorOptions: { browsers: ['chrome'], operatingSystems: ['macos'], locales: ['en-US'] }, body: HC_COMPANY_BODY(company), timeout: { request: 12_000 } });
    if (r.statusCode === 200 && r.body.trimStart().startsWith('{')) {
      type HCCo = { name?: string; totalActiveListings?: number; activeListings?: number; jobCount?: number };
      const token = companyMatchToken(company);
      const companies: HCCo[] = (JSON.parse(r.body) as { results?: HCCo[] }).results ?? [];
      const matched = companies.filter(co => (co.name ?? '').toLowerCase().includes(token));
      if (matched.length > 0) {
        const total = matched.reduce((s, co) => s + (co.totalActiveListings ?? co.activeListings ?? co.jobCount ?? 0), 0);
        log.debug(`HC signal via search-companies for ${company}: ${total} listings`);
        return { found: true, activeListings: total, avgViews: 0, avgApplications: 0, lowEngagement: total === 0, signal: total === 0 ? `hiring.cafe(companies):found,0listings—low` : total > 20 ? `hiring.cafe(companies):${total}listings—genuine` : null };
      }
    }
  } catch (e) { log.debug(`HC signal search-companies: ${e}`); }

  // Strategy 3: Wayback CDX — check both search-jobs and search-companies archived snapshots
  try {
    const fromTs = new Date(Date.now() - 180*86400*1000).toISOString().slice(0,10).replace(/-/g,'');
    const [jobsCdx, cosCdx] = await Promise.all([
      fetch(`http://web.archive.org/cdx/search/cdx?url=hiring.cafe/api/search-jobs&output=json&fl=timestamp,length&filter=statuscode:200&limit=20&from=${fromTs}&collapse=digest`, { signal: AbortSignal.timeout(10_000) }).then(r => r.ok ? r.json() as Promise<string[][]> : Promise.resolve([])).catch(() => []),
      fetch(`http://web.archive.org/cdx/search/cdx?url=hiring.cafe/api/search-companies&output=json&fl=timestamp,length&filter=statuscode:200&limit=10&from=${fromTs}&collapse=digest`, { signal: AbortSignal.timeout(10_000) }).then(r => r.ok ? r.json() as Promise<string[][]> : Promise.resolve([])).catch(() => []),
    ]);

    // Check search-companies snapshots first (more targeted — company records directly)
    const cosSnaps = (Array.isArray(cosCdx) ? (cosCdx as string[][]).slice(1) : []).filter(r => parseInt(r[1]) > 1_000).sort((a,b)=>b[0].localeCompare(a[0]));
    const token = companyMatchToken(company);
    for (const [ts] of cosSnaps.slice(0, 5)) {
      try {
        const snap = await fetch(`https://web.archive.org/web/${ts}id_/https://hiring.cafe/api/search-companies`, { signal: AbortSignal.timeout(12_000), headers: { Accept: 'application/json' } });
        if (!snap.ok) continue;
        const text = await snap.text();
        type HCCo = { name?: string; totalActiveListings?: number; activeListings?: number };
        const companies: HCCo[] = (JSON.parse(text) as { results?: HCCo[] }).results ?? [];
        const matched = companies.filter(co => (co.name ?? '').toLowerCase().includes(token));
        if (matched.length > 0) {
          const total = matched.reduce((s, co) => s + (co.totalActiveListings ?? co.activeListings ?? 0), 0);
          log.debug(`HC signal via Wayback search-companies (${matched.length} matches) for ${company}`);
          return { found: true, activeListings: total, avgViews: 0, avgApplications: 0, lowEngagement: total === 0, signal: total === 0 ? `hiring.cafe(cached-co):found,0listings—low` : null };
        }
      } catch { continue; }
    }

    // Check search-jobs snapshots — each has different 80 results, check all for the company
    const jobSnaps = (Array.isArray(jobsCdx) ? (jobsCdx as string[][]).slice(1) : []).filter(r => parseInt(r[1]) > 5_000).sort((a,b)=>b[0].localeCompare(a[0]));
    const allMatchedJobs: { viewCount?: number; applicationCount?: number }[] = [];
    for (const [ts] of jobSnaps.slice(0, 8)) {
      try {
        const snap = await fetch(`https://web.archive.org/web/${ts}id_/https://hiring.cafe/api/search-jobs`, { signal: AbortSignal.timeout(15_000), headers: { Accept: 'application/json' } });
        if (!snap.ok) continue;
        const text = await snap.text();
        type HCJob = { viewCount?: number; applicationCount?: number; v5_processed_company_data?: { name?: string } };
        const allJobs: HCJob[] = (JSON.parse(text) as { results?: HCJob[] }).results ?? [];
        const matched = allJobs.filter(j => (j.v5_processed_company_data?.name ?? '').toLowerCase().includes(token));
        if (matched.length > 0) allMatchedJobs.push(...matched);
      } catch { continue; }
    }
    if (allMatchedJobs.length > 0) {
      const avgV = allMatchedJobs.reduce((s, j) => s + (j.viewCount ?? 0), 0) / allMatchedJobs.length;
      const avgA = allMatchedJobs.reduce((s, j) => s + (j.applicationCount ?? 0), 0) / allMatchedJobs.length;
      const allZero = allMatchedJobs.length >= 3 && allMatchedJobs.every(j => (j.applicationCount ?? 0) === 0);
      const low = (avgV < 5 && avgA === 0) || allZero;
      log.debug(`HC signal via Wayback search-jobs (${allMatchedJobs.length} matches) for ${company}`);
      return { found: true, activeListings: allMatchedJobs.length, avgViews: avgV, avgApplications: avgA, lowEngagement: low, signal: low ? `hiring.cafe(cached):${allMatchedJobs.length}j avg${avgV.toFixed(1)}v—low` : null };
    }
  } catch (e) { log.debug(`HC signal wayback: ${e}`); }

  // Strategy 4: CDX company profile URL check — fast, no live API needed
  // Converts the company name to slug variants and checks if any profile page was archived.
  // Much more reliable than API strategies since CDX never rate-limits.
  try {
    const slugVariants = companyNameToSlugVariants(company);
    for (const slug of slugVariants) {
      const cdxUrl = `http://web.archive.org/cdx/search/cdx?url=hiring.cafe/companies/${slug}*&output=json&fl=timestamp&filter=statuscode:200&limit=5&collapse=urlkey`;
      const cdxRes = await fetch(cdxUrl, { signal: AbortSignal.timeout(8_000) }).catch(() => null);
      if (!cdxRes?.ok) continue;
      const rows = (await cdxRes.json() as string[][]).slice(1);
      if (!rows.length) continue;

      // Company profile exists on hiring.cafe — try to extract active listing count from archived page
      const latestTs = rows.sort((a, b) => b[0].localeCompare(a[0]))[0][0];
      let listings = 0;
      try {
        const html = await fetch(`https://web.archive.org/web/${latestTs}id_/https://hiring.cafe/companies/${slug}`, { signal: AbortSignal.timeout(15_000) })
          .then(r => r.ok ? r.text() : null).catch(() => null);
        if (html) {
          // Parse Next.js __NEXT_DATA__ for structured company/job count data
          // Find the script block start, then use JSON.parse with brace counting
          const ndIdx = html.indexOf('__NEXT_DATA__');
          if (ndIdx !== -1) {
            const jsonStart = html.indexOf('{', ndIdx);
            if (jsonStart !== -1) {
              // Walk forward counting braces to find matching closing brace
              let depth = 0; let pos = jsonStart;
              for (; pos < Math.min(html.length, jsonStart + 2_000_000); pos++) {
                if (html[pos] === '{') depth++;
                else if (html[pos] === '}') { depth--; if (depth === 0) break; }
              }
              if (depth === 0) {
                try {
                  const nd = JSON.parse(html.slice(jsonStart, pos + 1)) as Record<string, unknown>;
                  listings = extractListingsCountFromNextData(nd);
                } catch { /* parse error */ }
              }
            }
          }
          if (!listings) {
            // Fallback: regex scan for "N jobs" / "N openings" in page text
            const m = /\b(\d{1,4})\s+(?:active\s+)?(?:jobs?|openings?|listings?|positions?)/i.exec(html);
            if (m) listings = parseInt(m[1]);
          }
        }
      } catch { /* can't get details, still report found */ }

      log.debug(`HC signal via CDX profile (${slug}) for ${company}: found, ${listings} listings`);
      return { found: true, activeListings: listings, avgViews: 0, avgApplications: 0, lowEngagement: listings === 0, signal: listings === 0 ? `hiring.cafe(profile):found,0active—low` : listings > 20 ? `hiring.cafe(profile):${listings}active—genuine` : null };
    }
  } catch (e) { log.debug(`HC signal CDX profile: ${e}`); }

  // Strategy 4.5: archived hiring.cafe _next/data viewjob JSON files
  // Each archived job page at hiring.cafe/_next/data/*/viewjob/*.json contains the full job object
  // including v5_processed_company_data.name, viewCount, applicationCount — pure CDX, no Cloudflare.
  try {
    const token = companyMatchToken(company);
    const cdxUrl = `http://web.archive.org/cdx/search/cdx?url=hiring.cafe/_next/data/*&output=json&fl=original,timestamp&filter=statuscode:200&collapse=urlkey&limit=400`;
    const cdxRes = await fetch(cdxUrl, { signal: AbortSignal.timeout(12_000) }).catch(() => null);
    if (cdxRes?.ok) {
      const rows = ((await cdxRes.json() as string[][]).slice(1)).filter(r => r[0].includes('/viewjob/'));
      type HCNextJob = { v5_processed_company_data?: { name?: string }; viewCount?: number; applicationCount?: number };
      type HCNextData = { pageProps?: { job?: HCNextJob } };
      const matchedJobs: HCNextJob[] = [];
      for (const [fileUrl, ts] of rows) {
        try {
          const wbUrl = `https://web.archive.org/web/${ts}id_/${fileUrl}`;
          const resp = await fetch(wbUrl, { signal: AbortSignal.timeout(15_000), headers: { Accept: 'application/json' } });
          if (!resp.ok) continue;
          const raw = await resp.arrayBuffer();
          let text: string;
          // Detect gzip magic bytes (0x1f 0x8b)
          const bytes = new Uint8Array(raw.slice(0, 2));
          if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
            // gzip decompress using DecompressionStream if available, otherwise skip
            try {
              const ds = new DecompressionStream('gzip');
              const writer = ds.writable.getWriter();
              writer.write(raw); writer.close();
              const chunks: Uint8Array[] = [];
              const reader = ds.readable.getReader();
              let done = false;
              while (!done) { const result = await reader.read(); if (result.done) { done = true; } else { chunks.push(result.value); } }
              text = new TextDecoder().decode(new Uint8Array(chunks.reduce((acc, c) => [...acc, ...c], [] as number[])));
            } catch { continue; }
          } else {
            text = new TextDecoder().decode(raw);
          }
          if (!text.trimStart().startsWith('{')) continue;
          const d = JSON.parse(text) as HCNextData;
          const job = d.pageProps?.job;
          if (!job) continue;
          const name = (job.v5_processed_company_data?.name ?? '').toLowerCase();
          if (name.includes(token)) matchedJobs.push(job);
          // Stop early if we found enough matches
          if (matchedJobs.length >= 10) break;
        } catch { continue; }
      }
      if (matchedJobs.length > 0) {
        const avgV = matchedJobs.reduce((s, j) => s + (j.viewCount ?? 0), 0) / matchedJobs.length;
        const avgA = matchedJobs.reduce((s, j) => s + (j.applicationCount ?? 0), 0) / matchedJobs.length;
        const allZero = matchedJobs.length >= 2 && matchedJobs.every(j => (j.applicationCount ?? 0) === 0);
        const low = (avgV < 5 && avgA === 0) || allZero;
        log.debug(`HC signal via _next/data viewjob (${matchedJobs.length} matches) for ${company}`);
        return { found: true, activeListings: matchedJobs.length, avgViews: avgV, avgApplications: avgA, lowEngagement: low, signal: low ? `hiring.cafe(next-data):${matchedJobs.length}j avg${avgV.toFixed(1)}v—low` : avgA > 10 ? `hiring.cafe(next-data):${matchedJobs.length}j avg${avgA.toFixed(0)}apps—genuine` : null };
      }
    }
  } catch (e) { log.debug(`HC signal _next/data: ${e}`); }

  return null;
}

/** Generate slug variants from a company name for hiring.cafe profile URL lookup. */
function companyNameToSlugVariants(name: string): string[] {
  const base = name.toLowerCase()
    .replace(/\.(com|io|co|ai|net|org|app|dev|us|eu|de|fr|uk)\b/g, '') // drop TLDs
    .replace(/[.,&()'"/]/g, ' ')
    .replace(/\b(inc|llc|ltd|corp|ag|gmbh|sa|nv|plc|the|group|holdings?|technologies|solutions|services|systems|software|consulting)\b/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
  if (!base || base.length < 2) return [];
  const variants = [base];
  // Also try without trailing common suffixes that might differ
  const noSuffix = base.replace(/-(?:hq|us|global|labs|ai|tech)$/, '');
  if (noSuffix !== base && noSuffix.length >= 2) variants.push(noSuffix);
  return variants.slice(0, 3);
}

/** Walk a Next.js __NEXT_DATA__ object to find a job/listing count. */
function extractListingsCountFromNextData(obj: unknown, depth = 0): number {
  if (depth > 8 || !obj || typeof obj !== 'object') return 0;
  if (Array.isArray(obj)) {
    // If this looks like a jobs array, use its length
    if (obj.length > 0 && typeof obj[0] === 'object' && obj[0] !== null && ('title' in (obj[0] as object) || 'jobTitle' in (obj[0] as object))) return obj.length;
    return Math.max(...obj.map(item => extractListingsCountFromNextData(item, depth + 1)));
  }
  const record = obj as Record<string, unknown>;
  for (const key of ['totalActiveListings', 'activeListings', 'jobCount', 'totalJobs', 'openPositions', 'activeJobCount']) {
    if (typeof record[key] === 'number') return record[key] as number;
  }
  let best = 0;
  for (const val of Object.values(record)) {
    if (val && typeof val === 'object') {
      const sub = extractListingsCountFromNextData(val, depth + 1);
      if (sub > best) best = sub;
    }
  }
  return best;
}
