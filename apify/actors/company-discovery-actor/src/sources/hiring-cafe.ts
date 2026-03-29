import { Actor, log } from 'apify';
import { chromium } from 'playwright-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { sleep } from '../http.js';

chromium.use(StealthPlugin());
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

/**
 * Discover companies from hiring.cafe by aggregating job counts per company.
 *
 * Uses Playwright (Firefox) to pass Cloudflare's JS challenge, then makes
 * API calls via the browser's fetch API from within the page context.
 *
 * Persists per-company job counts to the shared KV store so subsequent runs
 * can report the delta (growing / shrinking / stable hiring activity).
 */
export async function discoverFromHiringCafe(maxPages = 20): Promise<CompanyDiscovery[]> {
  log.info(`hiring.cafe: fetching up to ${maxPages} pages (1 000 jobs each) via Playwright Firefox`);

  // Resolve proxy — Cloudflare blocks datacenter IPs, try residential first
  let proxyUrl: string | undefined;
  try {
    const proxyCfg = await Actor.createProxyConfiguration({ groups: ['RESIDENTIAL'] });
    proxyUrl = await proxyCfg.newUrl();
    log.info('hiring.cafe: using residential proxy');
  } catch {
    try {
      const proxyCfg = await Actor.createProxyConfiguration();
      proxyUrl = await proxyCfg.newUrl();
      log.warning('hiring.cafe: residential unavailable — using datacenter proxy (may be blocked)');
    } catch {
      log.warning('hiring.cafe: no proxy available — direct connection (may be blocked)');
    }
  }

  // headless: false + Xvfb virtual display (provided by apify/actor-node-playwright-chrome image)
  // + playwright-extra stealth plugin to hide automation markers from Cloudflare.
  const launchOptions: Parameters<typeof chromium.launch>[0] = { headless: false };
  if (proxyUrl) {
    const parsed = new URL(proxyUrl);
    launchOptions.proxy = {
      server: `${parsed.protocol}//${parsed.hostname}:${parsed.port}`,
      username: parsed.username || undefined,
      password: parsed.password || undefined,
    };
  }

  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    locale: 'en-US',
    viewport: { width: 1920, height: 1080 },
    extraHTTPHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
  });

  try {
    // Navigate to the homepage so Cloudflare can run its JS challenge and set cf_clearance
    log.info('hiring.cafe: opening homepage to pass Cloudflare challenge…');
    const page = await context.newPage();
    await page.goto('https://hiring.cafe', { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // Wait up to 30s for CF challenge to resolve (title changes from "Just a moment")
    try {
      await page.waitForFunction(
        () => !document.title.toLowerCase().includes('just a moment'),
        { timeout: 30_000 },
      );
      log.info(`hiring.cafe: challenge passed (page title: "${await page.title()}")`);
    } catch {
      log.warning(`hiring.cafe: CF challenge timed out (title: "${await page.title()}") — continuing anyway`);
    }

    // Extra breathing room after challenge resolution before hitting the API
    await sleep(2_000);

    const counts = new Map<string, number>();
    const now = new Date().toISOString();
    let totalJobs = 0;

    for (let pageIdx = 0; pageIdx < maxPages; pageIdx++) {
      const body = JSON.stringify({ size: 1000, page: pageIdx, searchState: SEARCH_STATE });

      let jobs: HiringCafeJob[] = [];
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          // Use page.evaluate so the request goes through the browser (with CF cookies + fingerprint)
          const result = await page.evaluate(async (payload: string) => {
            const resp = await fetch('https://hiring.cafe/api/search-jobs', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
              },
              body: payload,
            });
            return { status: resp.status, text: await resp.text() };
          }, body);

          if (result.status === 429) {
            const wait = 15_000 * (attempt + 1);
            log.warning(`hiring.cafe: rate-limited on page ${pageIdx}, waiting ${wait / 1000}s`);
            await sleep(wait);
            continue;
          }

          if (result.status !== 200) {
            log.warning(`hiring.cafe: page ${pageIdx} HTTP ${result.status} (attempt ${attempt + 1})`);
            await sleep(5_000 * (attempt + 1));
            continue;
          }

          if (!result.text || result.text.trimStart().startsWith('<')) {
            log.warning(`hiring.cafe: page ${pageIdx} returned HTML not JSON (attempt ${attempt + 1})`);
            await sleep(5_000 * (attempt + 1));
            continue;
          }

          const data: unknown = JSON.parse(result.text);
          jobs = extractJobs(data);
          break;
        } catch (err) {
          log.warning(`hiring.cafe: page ${pageIdx} error (attempt ${attempt + 1}): ${err}`);
          await sleep(3_000 * (attempt + 1));
        }
      }

      if (jobs.length === 0) {
        log.info(`hiring.cafe: empty page ${pageIdx} — stopping early`);
        break;
      }

      for (const job of jobs) {
        const name = job.source?.trim();
        if (!name || name.length < 2) continue;
        counts.set(name, (counts.get(name) ?? 0) + 1);
        totalJobs++;
      }

      log.info(`hiring.cafe: page ${pageIdx + 1}/${maxPages} — ${jobs.length} jobs, ${counts.size} companies`);

      if (jobs.length < 1000) break; // last page
      await sleep(800);
    }

    log.info(`hiring.cafe: scraped ${counts.size} unique companies across ${totalJobs} jobs`);

    // ── Load previous counts for delta tracking ─────────────────────────────
    const store = await Actor.openKeyValueStore(KV_STORE_NAME);
    const prev: Record<string, number> = (await store.getValue<Record<string, number>>(HC_COUNTS_KEY)) ?? {};

    // ── Build CompanyDiscovery records ───────────────────────────────────────
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

    // ── Persist updated counts ───────────────────────────────────────────────
    const newCounts: Record<string, number> = {};
    for (const [name, count] of counts) newCounts[name] = count;
    await store.setValue(HC_COUNTS_KEY, newCounts);
    log.info(`hiring.cafe: saved counts for ${Object.keys(newCounts).length} companies → KV:${HC_COUNTS_KEY}`);

    results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
    return results;

  } finally {
    await browser.close();
  }
}
