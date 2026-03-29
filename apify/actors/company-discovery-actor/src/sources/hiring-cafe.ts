/**
 * hiring.cafe discovery source
 *
 * Strategy: TLS-level Chrome fingerprinting via node-tls-client.
 * Past attempts (gotScraping, Playwright + stealth, residential proxies) all failed
 * because Cloudflare Bot Fight Mode checks the TLS/HTTP2 fingerprint at the socket
 * level before even running JS challenges. node-tls-client sends an exact Chrome 120
 * TLS ClientHello + HTTP2 SETTINGS frames, which makes CF treat the connection as a
 * real browser and skip the managed challenge entirely.
 */
import { Actor, log } from 'apify';
import { Session, initTLS } from 'node-tls-client';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const KV_STORE_NAME = 'company-discovery-portals';
const HC_COUNTS_KEY  = 'hiring_cafe_job_counts';

const SEARCH_STATE = {
  workplaceTypes:   ['remote', 'hybrid', 'onsite'],
  commitmentTypes:  ['fullTime', 'partTime', 'contract', 'internship', 'temporary'],
  dateFetchedPastNDays: 90,
};

interface HiringCafeJob {
  source?: string;
  id?: string;
  apply_url?: string;
}

function extractJobs(data: unknown): HiringCafeJob[] {
  if (!data || typeof data !== 'object') return [];
  const d = data as Record<string, unknown>;
  for (const key of ['results', 'jobs', 'data', 'items', 'content']) {
    if (Array.isArray(d[key])) return d[key] as HiringCafeJob[];
  }
  const hits = d['hits'] as Record<string, unknown> | undefined;
  if (Array.isArray(hits?.['hits'])) {
    return (hits!['hits'] as Array<Record<string, unknown>>)
      .map(h => h['_source'])
      .filter(Boolean) as HiringCafeJob[];
  }
  return [];
}

async function fetchPage(session: Session, page: number): Promise<HiringCafeJob[]> {
  const body = JSON.stringify({ size: 1000, page, searchState: SEARCH_STATE });

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await session.post('https://hiring.cafe/api/search-jobs', {
        headers: {
          'Content-Type':    'application/json',
          'Accept':          'application/json, text/plain, */*',
          'Accept-Language': 'en-US,en;q=0.9',
          'Accept-Encoding': 'gzip, deflate, br',
          'Origin':          'https://hiring.cafe',
          'Referer':         'https://hiring.cafe/',
          'Sec-Fetch-Dest':  'empty',
          'Sec-Fetch-Mode':  'cors',
          'Sec-Fetch-Site':  'same-origin',
          'Sec-Ch-Ua':       '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"',
          'Sec-Ch-Ua-Mobile': '?0',
          'Sec-Ch-Ua-Platform': '"Windows"',
        },
        body,
      });

      if (resp.status === 429) {
        const wait = 15_000 * (attempt + 1);
        log.warning(`hiring.cafe: rate-limited on page ${page}, waiting ${wait / 1000}s`);
        await sleep(wait);
        continue;
      }

      if (resp.status !== 200) {
        const snippet = resp.body?.slice(0, 200).replace(/\s+/g, ' ') ?? '';
        log.warning(`hiring.cafe: page ${page} HTTP ${resp.status} (attempt ${attempt + 1}) — ${snippet}`);
        await sleep(5_000 * (attempt + 1));
        continue;
      }

      const text = resp.body ?? '';
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

export async function discoverFromHiringCafe(maxPages = 20): Promise<CompanyDiscovery[]> {
  log.info(`hiring.cafe: fetching up to ${maxPages} pages via TLS-fingerprint (node-tls-client chrome_120)`);

  // node-tls-client requires initTLS() before first use (loads the Go shared library)
  await initTLS();

  // node-tls-client sends exact Chrome 120 TLS ClientHello + HTTP2 frames,
  // bypassing Cloudflare Bot Fight Mode at the network layer without needing a browser.
  const session = new Session({
    tlsClientIdentifier: 'chrome_120',
    followRedirects: true,
    insecureSkipVerify: false,
    timeoutSeconds: 45,
  });

  // Warm up: GET the homepage first so CF sees a realistic browser session pattern
  // (page load → API call), not a cold API hit.
  try {
    log.info('hiring.cafe: warming up session with homepage GET…');
    const warmup = await session.get('https://hiring.cafe', {
      headers: {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Fetch-Dest':  'document',
        'Sec-Fetch-Mode':  'navigate',
        'Sec-Fetch-Site':  'none',
        'Sec-Ch-Ua':       '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Upgrade-Insecure-Requests': '1',
      },
    });
    log.info(`hiring.cafe: homepage warm-up status ${warmup.status} (${warmup.body?.slice(0, 60).replace(/\s+/g, ' ')})`);
    await sleep(1_500);
  } catch (err) {
    log.warning(`hiring.cafe: warm-up failed: ${err}`);
  }

  const counts = new Map<string, number>();
  const now = new Date().toISOString();
  let totalJobs = 0;

  for (let page = 0; page < maxPages; page++) {
    const jobs = await fetchPage(session, page);

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

    if (jobs.length < 1000) break;
    await sleep(800);
  }

  log.info(`hiring.cafe: scraped ${counts.size} unique companies across ${totalJobs} jobs`);

  const store = await Actor.openKeyValueStore(KV_STORE_NAME);
  const prev: Record<string, number> = (await store.getValue<Record<string, number>>(HC_COUNTS_KEY)) ?? {};

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

  const newCounts: Record<string, number> = {};
  for (const [name, count] of counts) newCounts[name] = count;
  await store.setValue(HC_COUNTS_KEY, newCounts);
  log.info(`hiring.cafe: saved counts for ${Object.keys(newCounts).length} companies → KV:${HC_COUNTS_KEY}`);

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  return results;
}
