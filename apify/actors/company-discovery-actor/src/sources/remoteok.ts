/**
 * RemoteOK — one of the largest remote job boards with a public JSON API.
 * API: https://remoteok.com/api (returns array of jobs, first item is metadata)
 */
import { log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

interface RemoteOKJob {
  id?: string;
  company?: string;
  company_logo?: string;
  position?: string;
  url?: string;
  date?: string;
}

export async function discoverFromRemoteOK(): Promise<CompanyDiscovery[]> {
  log.info('remoteok: fetching public API');

  let body = '';
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await gotScraping({
        url: 'https://remoteok.com/api',
        headers: { Accept: 'application/json', 'User-Agent': 'Mozilla/5.0 (compatible; JobseekBot/1.0)' },
        timeout: { request: 30_000 },
      });
      if (r.statusCode === 200) { body = r.body; break; }
      await sleep(3000 * (attempt + 1));
    } catch (e) {
      log.debug(`remoteok attempt ${attempt + 1}: ${e}`);
      if (attempt < 2) await sleep(3000 * (attempt + 1));
    }
  }

  if (!body) { log.warning('remoteok: no response'); return []; }

  let jobs: RemoteOKJob[];
  try {
    const raw = JSON.parse(body);
    // First item is metadata, skip it
    jobs = Array.isArray(raw) ? raw.slice(1).filter((j: RemoteOKJob) => j.company) : [];
  } catch (e) {
    log.warning(`remoteok: JSON parse failed: ${e}`);
    return [];
  }

  // Aggregate by company name
  const counts = new Map<string, { name: string; url: string; count: number }>();
  for (const job of jobs) {
    const name = job.company?.trim();
    if (!name || name.length < 2) continue;
    const key = name.toLowerCase();
    const ex = counts.get(key);
    if (ex) { ex.count++; }
    else counts.set(key, { name, url: job.url ?? `https://remoteok.com/@${encodeURIComponent(name.toLowerCase())}`, count: 1 });
  }

  log.info(`remoteok: ${counts.size} unique companies`);
  const now = new Date().toISOString();
  return [...counts.values()]
    .sort((a, b) => b.count - a.count)
    .map(c => ({
      company_name: c.name,
      job_board_url: c.url,
      estimated_jobs: c.count,
      source: 'remoteok' as const,
      discovered_at: now,
    })) as CompanyDiscovery[];
}
