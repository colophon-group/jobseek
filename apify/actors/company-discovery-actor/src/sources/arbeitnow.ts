import { fetchJson, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

interface ArbeitnowJob {
  slug: string;
  company_name: string;
  title: string;
  location: string;
  remote: boolean;
  url: string;
  tags: string[];
  created_at: number;
}

interface ArbeitnowResponse {
  data: ArbeitnowJob[];
  links: { next: string | null };
  meta: { current_page: number; per_page: number };
}

export async function discoverFromArbeitnow(maxPages = 80): Promise<CompanyDiscovery[]> {
  const companyCounts = new Map<string, { count: number; url: string }>();
  const now = new Date().toISOString();

  console.log('Arbeitnow: fetching job listings...');

  let totalJobs = 0;
  for (let page = 1; page <= maxPages; page++) {
    const url = `https://www.arbeitnow.com/api/job-board-api?page=${page}`;
    const data = await fetchJson<ArbeitnowResponse>(url);

    if (!data?.data?.length) break;

    for (const job of data.data) {
      const name = job.company_name?.trim();
      if (!name || name.length < 2) continue;

      const key = name.toLowerCase();
      const existing = companyCounts.get(key);
      if (existing) {
        existing.count++;
      } else {
        companyCounts.set(key, { count: 1, url: job.url || '' });
      }
      totalJobs++;
    }

    if (page % 10 === 0) console.log(`Arbeitnow: page ${page}, ${totalJobs} jobs, ${companyCounts.size} companies`);

    if (!data.links.next) break;
    await sleep(300);
  }

  // Convert to CompanyDiscovery array
  const results: CompanyDiscovery[] = [];
  for (const [key, { count, url }] of companyCounts) {
    // Capitalize company name properly
    const name = key.replace(/\b\w/g, c => c.toUpperCase());
    results.push({
      company_name: name,
      job_board_url: url || `https://www.arbeitnow.com/companies/${encodeURIComponent(key)}`,
      estimated_jobs: count,
      source: 'arbeitnow',
      discovered_at: now,
    });
  }

  // Sort by job count descending
  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);

  const total = results.reduce((sum, c) => sum + c.estimated_jobs, 0);
  console.log(`Arbeitnow: ${results.length} companies, ${total.toLocaleString()} total jobs`);

  return results;
}
