import { Actor, log } from 'apify';
import { gotScraping } from 'got-scraping';
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

async function fetchPage(page: number, proxyUrl?: string): Promise<HiringCafeJob[]> {
  const body = JSON.stringify({ size: 1000, page, searchState: SEARCH_STATE });

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await gotScraping({
        url:      'https://hiring.cafe/api/search-jobs',
        method:   'POST',
        proxyUrl,
        body,
        headers: {
          'Content-Type':    'application/json',
          'Accept':          'application/json, text/plain, */*',
          'Accept-Language': 'en-US,en;q=0.9',
          'Origin':          'https://hiring.cafe',
          'Referer':         'https://hiring.cafe/',
          'Sec-Fetch-Dest':  'empty',
          'Sec-Fetch-Mode':  'cors',
          'Sec-Fetch-Site':  'same-origin',
        },
        headerGeneratorOptions: {
          browsers:         ['chrome'],
          operatingSystems: ['windows', 'macos'],
          locales:          ['en-US'],
        },
        timeout:        { request: 45_000 },
        followRedirect: true,
      });

      if (resp.statusCode === 429) {
        const wait = 15_000 * (attempt + 1);
        log.warning(`hiring.cafe: rate limited on page ${page}, waiting ${wait / 1000}s`);
        await sleep(wait);
        continue;
      }

      // Retry on server errors; Cloudflare 403/503 also land here
      if (resp.statusCode !== 200) {
        log.warning(`hiring.cafe: page ${page} HTTP ${resp.statusCode} (attempt ${attempt + 1})`);
        await sleep(5_000 * (attempt + 1));
        continue;
      }

      // Guard against Cloudflare HTML challenge slipping through as 200
      const text = resp.body;
      if (!text || text.trimStart().startsWith('<')) {
        log.warning(`hiring.cafe: page ${page} got HTML not JSON (attempt ${attempt + 1})`);
        await sleep(5_000 * (attempt + 1));
        continue;
      }

      const data: unknown = JSON.parse(text);
      return extractJobs(data);
    } catch (err) {
      log.warning(`hiring.cafe: page ${page} error (attempt ${attempt + 1}): ${err}`);
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

  // Apify proxy to bypass Cloudflare (datacenter proxies are included on all plans)
  let proxyUrl: string | undefined;
  try {
    const proxyCfg = await Actor.createProxyConfiguration();
    proxyUrl = await proxyCfg.newUrl();
    log.info('hiring.cafe: using Apify proxy to bypass Cloudflare');
  } catch {
    log.warning('hiring.cafe: no proxy available — requests may be blocked by Cloudflare');
  }

  const counts = new Map<string, number>();
  const now = new Date().toISOString();
  let totalJobs = 0;

  for (let page = 0; page < maxPages; page++) {
    const jobs = await fetchPage(page, proxyUrl);

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
