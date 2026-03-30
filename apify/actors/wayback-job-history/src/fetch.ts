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
const HC_BODY = (q: string) => JSON.stringify({ searchQuery: q, filters: [], page: 0, pageSize: 20, sortBy: 'date' });
const HC_HEADERS = { 'Content-Type': 'application/json', 'Accept': 'application/json', 'Origin': 'https://hiring.cafe', 'Referer': 'https://hiring.cafe/' };

/** Extract the most distinctive token from a company name for fuzzy matching. */
function companyMatchToken(name: string): string {
  const cleaned = name.toLowerCase()
    .replace(/[.,&()'/]/g, '')   // strip punctuation
    .replace(/\b(inc|llc|ltd|corp|co|ag|gmbh|sa|nv|plc|group|holdings?|the)\b/g, '') // strip legal suffixes
    .trim();
  // Use first word of ≥4 chars, or full cleaned string up to 12 chars
  const firstWord = cleaned.split(/\s+/).find(w => w.length >= 4) ?? cleaned;
  return firstWord.slice(0, 12);
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

  // Strategy 3: Wayback CDX — find a recent cached snapshot of the search API response
  try {
    const cdxRes = await fetch(
      `http://web.archive.org/cdx/search/cdx?url=hiring.cafe/api/search-jobs&output=json&fl=timestamp,length&filter=statuscode:200&limit=5&from=${new Date(Date.now() - 90*86400*1000).toISOString().slice(0,10).replace(/-/g,'')}&collapse=digest`,
      { signal: AbortSignal.timeout(10_000) },
    );
    if (cdxRes.ok) {
      const rows: string[][] = await cdxRes.json();
      const snaps = rows.slice(1).filter(r => parseInt(r[1]) > 10_000).sort((a, b) => b[0].localeCompare(a[0]));
      for (const [ts] of snaps.slice(0, 3)) {
        try {
          const snap = await fetch(`https://web.archive.org/web/${ts}id_/https://hiring.cafe/api/search-jobs`, { signal: AbortSignal.timeout(15_000), headers: { Accept: 'application/json' } });
          if (!snap.ok) continue;
          // This is a generic snapshot — search within it for company mentions
          const text = await snap.text();
          type HCJob = { viewCount?: number; applicationCount?: number; v5_processed_company_data?: { name?: string } };
          const allJobs: HCJob[] = (JSON.parse(text) as { results?: HCJob[] }).results ?? [];
          const jobs = allJobs.filter(j => (j.v5_processed_company_data?.name ?? '').toLowerCase().includes(companyMatchToken(company)));
          if (jobs.length > 0) {
            const avgV = jobs.reduce((s, j) => s + (j.viewCount ?? 0), 0) / jobs.length;
            const avgA = jobs.reduce((s, j) => s + (j.applicationCount ?? 0), 0) / jobs.length;
            const allZero = jobs.length >= 3 && jobs.every(j => (j.applicationCount ?? 0) === 0);
            const low = (avgV < 5 && avgA === 0) || allZero;
            log.debug(`HC signal via Wayback snapshot ${ts} for ${company}`);
            return { found: true, activeListings: jobs.length, avgViews: avgV, avgApplications: avgA, lowEngagement: low, signal: low ? `hiring.cafe(cached):${jobs.length}j avg${avgV.toFixed(1)}v—low` : null };
          }
          break; // Snapshot found but company not in it — no point checking older ones
        } catch { continue; }
      }
    }
  } catch (e) { log.debug(`HC signal wayback: ${e}`); }

  return null;
}
