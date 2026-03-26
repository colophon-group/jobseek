import { fetchJson, processInBatches, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

interface MuseCompany {
  id: number;
  name: string;
  short_name: string;
  size?: { name: string; short_name: string };
  industries?: Array<{ name: string }>;
  locations?: Array<{ name: string }>;
  refs?: { jobs_page?: string; landing_page?: string };
}

interface MuseCompaniesResponse {
  page: number;
  page_count: number;
  total: number;
  results: MuseCompany[];
}

interface MuseJobsResponse {
  total: number;
  page_count: number;
}

// Map company size to estimated open positions (based on ~5-8% vacancy rate)
function estimateJobsFromSize(sizeName: string): number {
  const lower = sizeName.toLowerCase();
  if (lower.includes('micro') || lower.includes('1-10')) return 3;
  if (lower.includes('small') || lower.includes('11-50')) return 8;
  if (lower.includes('medium') || lower.includes('51-200')) return 25;
  if (lower.includes('large') && !lower.includes('extra') && !lower.includes('very')) return 60;
  if (lower.includes('very large') || lower.includes('501-1000')) return 120;
  if (lower.includes('1001-5000') || lower.includes('large size')) return 300;
  if (lower.includes('extra') || lower.includes('5001')) return 700;
  if (lower.includes('massive') || lower.includes('10001')) return 1500;
  return 20;
}

export async function discoverFromTheMuse(maxCompanies = 1000): Promise<CompanyDiscovery[]> {
  const results: CompanyDiscovery[] = [];
  const now = new Date().toISOString();

  // Step 1: Fetch all companies from the browse API
  console.log('TheMuse: fetching company directory...');
  const companies: MuseCompany[] = [];

  // First fetch to get total page count
  const first = await fetchJson<MuseCompaniesResponse>('https://www.themuse.com/api/public/companies?page=0');
  if (!first) {
    console.log('TheMuse: failed to fetch first page');
    return [];
  }

  companies.push(...first.results);
  console.log(`TheMuse: total=${first.total}, pages=${first.page_count}`);

  // Fetch remaining pages
  for (let page = 1; page < first.page_count && companies.length < maxCompanies; page++) {
    const data = await fetchJson<MuseCompaniesResponse>(
      `https://www.themuse.com/api/public/companies?page=${page}`,
    );
    if (!data?.results?.length) break;
    companies.push(...data.results);
    if (page % 10 === 0) console.log(`TheMuse: fetched page ${page}/${first.page_count} (${companies.length} companies)`);
    await sleep(200);
  }

  console.log(`TheMuse: fetched ${companies.length} companies, now checking job counts...`);

  // Step 2: For each company, try to get actual job count from the jobs endpoint
  const companyBatch = companies.slice(0, maxCompanies);

  const discoveries = await processInBatches(companyBatch, 10, async (company) => {
    const slug = company.short_name;
    const jobsUrl = `https://www.themuse.com/api/public/jobs?company=${encodeURIComponent(slug)}&page=0`;
    const jobsData = await fetchJson<MuseJobsResponse>(jobsUrl);

    // Use actual job count from API if available, otherwise estimate from size
    let estimatedJobs = jobsData?.total ?? 0;
    if (estimatedJobs === 0 && company.size?.name) {
      estimatedJobs = estimateJobsFromSize(company.size.name);
    }
    if (estimatedJobs === 0) estimatedJobs = 10; // reasonable default

    return {
      company_name: company.name,
      job_board_url: company.refs?.jobs_page || company.refs?.landing_page || `https://www.themuse.com/companies/${slug}`,
      estimated_jobs: estimatedJobs,
      source: 'themuse',
      discovered_at: now,
    } as CompanyDiscovery;
  });

  for (const d of discoveries) {
    if (d) results.push(d);
  }

  const total = results.reduce((sum, c) => sum + c.estimated_jobs, 0);
  console.log(`TheMuse: ${results.length} companies, ${total.toLocaleString()} total estimated jobs`);

  return results;
}
