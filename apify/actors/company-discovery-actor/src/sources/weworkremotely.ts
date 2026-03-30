/**
 * We Work Remotely — largest fully remote job board (~250k monthly visitors).
 * Public RSS feeds by category: https://weworkremotely.com/categories/<slug>.rss
 */
import { log } from 'apify';
import { gotScraping } from 'got-scraping';
import { sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

const FEEDS = [
  'remote-full-stack-programming-jobs',
  'remote-back-end-programming-jobs',
  'remote-front-end-programming-jobs',
  'remote-devops-sysadmin-jobs',
  'remote-design-jobs',
  'remote-product-jobs',
  'remote-management-executive-jobs',
  'remote-sales-and-marketing-jobs',
  'remote-data-science-jobs',
  'remote-customer-support-jobs',
  'remote-all-other-remote-jobs',
];

function extractCompany(item: string): string | null {
  const m = item.match(/<company><!\[CDATA\[([^\]]+)\]\]><\/company>/) ??
            item.match(/<region><!\[CDATA\[([^\]]+)\]\]><\/region>/);
  // Actually WWR doesn't expose company field cleanly in RSS — extract from title
  const t = item.match(/<title><!\[CDATA\[([^\]]+)\]\]><\/title>/);
  if (!t) return null;
  // Title format: "Company Name | Job Title"
  const parts = t[1].split(/\s*\|\s*/);
  return parts.length >= 2 ? parts[0].trim() : null;
}

export async function discoverFromWeWorkRemotely(): Promise<CompanyDiscovery[]> {
  log.info('weworkremotely: fetching RSS feeds');
  const counts = new Map<string, number>();

  for (const feed of FEEDS) {
    try {
      const r = await gotScraping({
        url: `https://weworkremotely.com/categories/${feed}.rss`,
        headers: { Accept: 'application/rss+xml,application/xml,text/xml' },
        timeout: { request: 15_000 },
      });
      if (r.statusCode !== 200) { await sleep(1000); continue; }
      // Parse <item> blocks
      const items = r.body.split('<item>').slice(1);
      for (const item of items) {
        const name = extractCompany(item);
        if (name && name.length > 1) counts.set(name, (counts.get(name) ?? 0) + 1);
      }
      await sleep(500);
    } catch (e) { log.debug(`wwr feed ${feed}: ${e}`); }
  }

  log.info(`weworkremotely: ${counts.size} companies`);
  const now = new Date().toISOString();
  return [...counts.entries()]
    .map(([name, cnt]) => ({
      company_name: name,
      job_board_url: `https://weworkremotely.com/?term=${encodeURIComponent(name)}`,
      estimated_jobs: cnt,
      source: 'weworkremotely' as const,
      discovered_at: now,
    }))
    .sort((a, b) => b.estimated_jobs - a.estimated_jobs) as CompanyDiscovery[];
}
