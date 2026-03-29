import { Actor, log } from 'apify';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

// KV key within the shared company-discovery-portals store
const KV_STORE_NAME = 'company-discovery-portals';
const HC_COUNTS_KEY  = 'hiring_cafe_job_counts';

// Broad search state — no location/seniority filter so we see everything
const SEARCH_STATE = {
  workplaceTypes:   ['remote', 'hybrid', 'onsite'],
  commitmentTypes:  ['fullTime', 'partTime', 'contract', 'internship', 'temporary'],
  dateFetchedPastNDays: 90,
};

const HEADERS: Record<string, string> = {
  'Content-Type':       'application/json',
  'Accept':             'application/json, text/plain, */*',
  'Accept-Language':    'en-US,en;q=0.9',
  'Accept-Encoding':    'gzip, deflate, br',
  'Origin':             'https://hiring.cafe',
  'Referer':            'https://hiring.cafe/',
  'User-Agent':         'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'sec-ch-ua':          '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
  'sec-ch-ua-mobile':   '?0',
  'sec-ch-ua-platform': '"Windows"',
  'Sec-Fetch-Dest':     'empty',
  'Sec-Fetch-Mode':     'cors',
  'Sec-Fetch-Site':     'same-origin',
};

interface HiringCafeJob {
  source?: string;
  id?: string;
  apply_url?: string;
  viewedByUsers?: number;
  appliedFromUsers?: number;
  savedFromUsers?: number;
}

// hiring.cafe returns different shapes across versions — handle all
function extractJobs(data: unknown): HiringCafeJob[] {
  if (!data || typeof data !== 'object') return [];
  const d = data as Record<string, unknown>;
  for (const key of ['results', 'jobs', 'data', 'items', 'content']) {
    if (Array.isArray(d[key])) return d[key] as HiringCafeJob[];
  }
  // Elasticsearch-style hits
  const hits = d['hits'] as Record<string, unknown> | undefined;
  if (Array.isArray(hits?.['hits'])) {
    return (hits!['hits'] as Array<Record<string, unknown>>)
      .map(h => h['_source'])
      .filter(Boolean) as HiringCafeJob[];
  }
  return [];
}

async function fetchPage(page: number): Promise<HiringCafeJob[]> {
  const body = JSON.stringify({ size: 1000, page, searchState: SEARCH_STATE });

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await fetch('https://hiring.cafe/api/search-jobs', {
        method:  'POST',
        headers: HEADERS,
        body,
        signal: AbortSignal.timeout(45_000),
      });

      if (resp.status === 429) {
        const wait = 15_000 * (attempt + 1);
        log.warning(`hiring.cafe: rate limited on page ${page}, waiting ${wait / 1000}s`);
        await sleep(wait);
        continue;
      }

      if (!resp.ok) {
        log.debug(`hiring.cafe: page ${page} returned HTTP ${resp.status}`);
        return [];
      }

      const data: unknown = await resp.json();
      return extractJobs(data);
    } catch (err) {
      log.debug(`hiring.cafe: page ${page} error (attempt ${attempt + 1}): ${err}`);
      await sleep(3_000 * (attempt + 1));
    }
  }
  return [];
}

/**
 * Discover companies from hiring.cafe by aggregating job counts per company.
 *
 * Persists per-company job counts to the shared KV store so subsequent runs
 * can report the delta (growing / shrinking / stable hiring activity).
 */
export async function discoverFromHiringCafe(maxPages = 20): Promise<CompanyDiscovery[]> {
  log.info(`hiring.cafe: fetching up to ${maxPages} pages (1 000 jobs each)`);

  const counts = new Map<string, number>();
  const now = new Date().toISOString();
  let totalJobs = 0;

  for (let page = 0; page < maxPages; page++) {
    const jobs = await fetchPage(page);

    if (jobs.length === 0) {
      log.info(`hiring.cafe: empty page ${page} — stopping early`);
      break;
    }

    for (const job of jobs) {
      const name = job.source?.trim();
      if (!name || name.length < 2) continue;
      counts.set(name, (counts.get(name) ?? 0) + 1);
      totalJobs++;
    }

    log.info(`hiring.cafe: page ${page + 1}/${maxPages} — ${jobs.length} jobs, ${counts.size} companies`);

    if (jobs.length < 1000) break; // last page
    await sleep(800);
  }

  log.info(`hiring.cafe: scraped ${counts.size} unique companies across ${totalJobs} jobs`);

  // ── Load previous counts for delta tracking ───────────────────────────────
  const store = await Actor.openKeyValueStore(KV_STORE_NAME);
  const prev: Record<string, number> = (await store.getValue<Record<string, number>>(HC_COUNTS_KEY)) ?? {};

  // ── Build CompanyDiscovery records ────────────────────────────────────────
  const results: CompanyDiscovery[] = [];

  for (const [name, count] of counts) {
    const prevCount = prev[name] ?? null;
    results.push({
      company_name:   name,
      job_board_url:  `https://hiring.cafe/?q=${encodeURIComponent(name)}`,
      estimated_jobs: count,
      source:         'hiring-cafe',
      discovered_at:  now,
      prev_jobs:      prevCount,
      jobs_delta:     prevCount !== null ? count - prevCount : null,
    } as CompanyDiscovery);
  }

  // ── Persist updated counts ────────────────────────────────────────────────
  const newCounts: Record<string, number> = {};
  for (const [name, count] of counts) newCounts[name] = count;
  await store.setValue(HC_COUNTS_KEY, newCounts);
  log.info(`hiring.cafe: saved counts for ${Object.keys(newCounts).length} companies → KV:${HC_COUNTS_KEY}`);

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  return results;
}
