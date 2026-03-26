import { fetchJson } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

interface RemotiveJob {
  id: number;
  url: string;
  title: string;
  company_name: string;
  company_logo: string;
  category: string;
  publication_date: string;
}

interface RemotiveResponse {
  'job-count': number;
  jobs: RemotiveJob[];
}

export async function discoverFromRemotive(): Promise<CompanyDiscovery[]> {
  const now = new Date().toISOString();

  console.log('Remotive: fetching remote jobs...');
  const data = await fetchJson<RemotiveResponse>('https://remotive.com/api/remote-jobs?limit=10000');

  if (!data?.jobs?.length) {
    console.log('Remotive: no data returned');
    return [];
  }

  // Aggregate by company
  const companyCounts = new Map<string, { count: number; name: string; url: string }>();
  for (const job of data.jobs) {
    const name = job.company_name?.trim();
    if (!name || name.length < 2) continue;

    const key = name.toLowerCase();
    const existing = companyCounts.get(key);
    if (existing) {
      existing.count++;
    } else {
      companyCounts.set(key, { count: 1, name, url: job.url || '' });
    }
  }

  const results: CompanyDiscovery[] = [];
  for (const [, { count, name, url }] of companyCounts) {
    results.push({
      company_name: name,
      job_board_url: url || `https://remotive.com`,
      estimated_jobs: count,
      source: 'remotive',
      discovered_at: now,
    });
  }

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);

  const total = results.reduce((sum, c) => sum + c.estimated_jobs, 0);
  console.log(`Remotive: ${results.length} companies, ${total.toLocaleString()} total jobs`);

  return results;
}
