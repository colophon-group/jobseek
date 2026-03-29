/**
 * hiring.cafe discovery source — Wayback Machine strategy
 *
 * Direct API access is blocked by Cloudflare Bot Fight Mode on all cloud/proxy IPs.
 * The Wayback Machine has cached 200+ snapshots of hiring.cafe/api/search-jobs
 * (up to Oct 2025, 100–700 KB each). We use CDX API to discover recent large
 * snapshots and fetch them directly — no Cloudflare involved.
 *
 * Each snapshot contains 80–160 jobs. By sampling ~25 snapshots spread across
 * recent months we aggregate 500–1 000 unique companies with job counts stored
 * in the KV store for delta tracking across actor runs.
 */
import { Actor, log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const KV_STORE_NAME = 'company-discovery-portals';
const HC_COUNTS_KEY  = 'hiring_cafe_job_counts';
const CDX_API = 'http://web.archive.org/cdx/search/cdx';
const WB_BASE = 'http://web.archive.org/web';

interface HiringCafeJob {
  v5_processed_company_data?: { name?: string };
  source?: string;
}

interface CdxSnapshot { timestamp: string; size: number }

/** Fetch list of snapshots via CDX API, sorted newest→oldest, filtered to real data (>100KB). */
async function listSnapshots(minSizeKb = 100): Promise<CdxSnapshot[]> {
  const url = `${CDX_API}?url=hiring.cafe/api/search-jobs&output=json&fl=timestamp,length&filter=statuscode:200&limit=500`;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await gotScraping({ url, timeout: { request: 30_000 } });
      if (resp.statusCode !== 200) continue;
      const rows: string[][] = JSON.parse(resp.body);
      return rows.slice(1) // skip header row
        .map(r => ({ timestamp: r[0], size: parseInt(r[1], 10) }))
        .filter(s => s.size >= minSizeKb * 1024)
        .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    } catch (err) {
      log.warning(`hiring.cafe/wayback: CDX list attempt ${attempt + 1} failed: ${err}`);
      if (attempt < 2) await sleep(3_000 * (attempt + 1));
    }
  }
  return [];
}

/** Fetch one Wayback snapshot and extract company names.
 *  Uses the `if_` modifier to bypass the Wayback Machine HTML wrapper
 *  and get the original raw JSON response directly.
 */
async function fetchSnapshot(ts: string): Promise<string[]> {
  // if_ tells Wayback Machine to serve original content, not the HTML-framed replay
  const url = `${WB_BASE}/${ts}if_/https://hiring.cafe/api/search-jobs`;
  try {
    const resp = await gotScraping({
      url,
      headers: {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
      },
      timeout: { request: 30_000 },
      followRedirect: true,
    });

    if (resp.statusCode !== 200) return [];

    const text = resp.body ?? '';
    if (!text.trimStart().startsWith('{') && !text.trimStart().startsWith('[')) {
      // Wayback returned its UI HTML wrapper instead of raw JSON
      return [];
    }

    const data = JSON.parse(text) as Record<string, unknown>;
    const jobs: HiringCafeJob[] = Array.isArray(data['results'])
      ? (data['results'] as HiringCafeJob[])
      : [];

    return jobs
      .map(j => j.v5_processed_company_data?.name?.trim() ?? j.source?.trim() ?? '')
      .filter(n => n.length > 1);
  } catch (err) {
    log.debug(`hiring.cafe/wayback: snapshot ${ts} error: ${err}`);
    return [];
  }
}

/**
 * Discover companies from hiring.cafe via Wayback Machine cached API snapshots.
 *
 * Fetches up to `maxSnapshots` recent large snapshots from archive.org/cdx,
 * aggregates company names, and persists job counts to KV for delta tracking.
 */
export async function discoverFromHiringCafe(maxSnapshots = 30): Promise<CompanyDiscovery[]> {
  log.info('hiring.cafe: discovering via Wayback Machine cached API snapshots');

  // ── Discover available snapshots ─────────────────────────────────────────
  const snapshots = await listSnapshots(100);
  log.info(`hiring.cafe/wayback: found ${snapshots.length} large snapshots (≥100 KB)`);

  if (snapshots.length === 0) {
    log.warning('hiring.cafe/wayback: no snapshots found — source skipped');
    return [];
  }

  // Recency-biased sampling: take the N most recent, then sample evenly from the rest
  // This ensures we capture fresh company data while still getting historical coverage.
  const recentCount = Math.min(Math.ceil(maxSnapshots / 2), snapshots.length);
  const recentSnaps = snapshots.slice(0, recentCount);
  const olderSnaps  = snapshots.slice(recentCount);
  const historyCount = maxSnapshots - recentCount;
  const step = historyCount > 0 ? Math.max(1, Math.floor(olderSnaps.length / historyCount)) : 1;
  const historySample = olderSnaps.filter((_, i) => i % step === 0).slice(0, historyCount);
  const selected = [...recentSnaps, ...historySample];
  log.info(`hiring.cafe/wayback: sampling ${selected.length} snapshots (${recentCount} recent + ${historySample.length} historical, step=${step})`);

  // ── Fetch snapshots ───────────────────────────────────────────────────────
  const counts = new Map<string, number>();
  const now = new Date().toISOString();
  let fetchedCount = 0;

  for (const snap of selected) {
    const names = await fetchSnapshot(snap.timestamp);
    if (names.length === 0) {
      log.debug(`hiring.cafe/wayback: ${snap.timestamp} — empty (skipped)`);
      await sleep(500);
      continue;
    }

    for (const name of names) {
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    fetchedCount++;
    log.info(`hiring.cafe/wayback: ${snap.timestamp} (${Math.round(snap.size / 1024)}KB) — ${names.length} companies, ${counts.size} unique so far`);
    await sleep(1_200); // be kind to archive.org
  }

  log.info(`hiring.cafe/wayback: fetched ${fetchedCount}/${selected.length} snapshots — ${counts.size} unique companies`);

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
  log.info(`hiring.cafe/wayback: saved counts for ${Object.keys(newCounts).length} companies → KV:${HC_COUNTS_KEY}`);

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  return results;
}
