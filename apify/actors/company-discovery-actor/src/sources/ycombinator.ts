/**
 * Y Combinator company directory source.
 *
 * YC maintains a public API at api.ycombinator.com/v0.1/companies
 * listing all funded companies (5,800+) with team size, batch, and status.
 *
 * Active companies get job count estimated from teamSize (5% vacancy rate).
 * Job board URL points to the YC company page #jobs tab.
 */
import { fetchJson, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

interface YCCompany {
  id: number;
  name: string;
  slug: string;
  website?: string;
  teamSize?: number;
  status?: string;    // 'Active' | 'Inactive' | 'Public' | 'Acquired' | ...
  batch?: string;
}

interface YCResponse {
  companies: YCCompany[];
  totalPages: number;
  nextPage: string | null;
  page: number;
}

function estimateJobs(teamSize: number | undefined): number {
  const size = teamSize ?? 10;
  // ~5-10% open positions depending on size
  if (size >= 1000) return Math.round(size * 0.06);
  if (size >= 200) return Math.round(size * 0.07);
  if (size >= 50) return Math.round(size * 0.08);
  if (size >= 10) return Math.round(size * 0.10);
  return 2; // seed-stage default
}

export async function discoverFromYCombinator(maxPages = 300): Promise<CompanyDiscovery[]> {
  const now = new Date().toISOString();
  const results: CompanyDiscovery[] = [];
  let page = 1;

  console.log('YCombinator: fetching company directory...');

  while (page <= maxPages) {
    const data = await fetchJson<YCResponse>(
      `https://api.ycombinator.com/v0.1/companies?page=${page}&per_page=100`,
    );

    if (!data?.companies?.length) break;

    for (const co of data.companies) {
      if (!co.name) continue;
      // Skip inactive/acquired companies unless they're well-known
      const status = (co.status ?? '').toLowerCase();
      if (status === 'inactive' || status === 'dead') continue;

      results.push({
        company_name: co.name,
        job_board_url: `https://www.ycombinator.com/companies/${co.slug}#jobs`,
        estimated_jobs: estimateJobs(co.teamSize),
        source: 'ycombinator',
        discovered_at: now,
      });
    }

    if (page % 50 === 0) {
      console.log(`YCombinator: page ${page}/${data.totalPages}, ${results.length} companies so far`);
    }

    if (!data.nextPage || page >= data.totalPages) break;
    page++;
    await sleep(150);
  }

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);
  const totalJobs = results.reduce((s, c) => s + c.estimated_jobs, 0);
  console.log(`YCombinator: ${results.length} active companies, ${totalJobs.toLocaleString()} estimated jobs`);

  return results;
}
