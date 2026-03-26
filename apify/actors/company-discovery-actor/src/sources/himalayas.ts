import { fetchJson, processInBatches, sleep } from '../http.js';
import type { CompanyDiscovery } from '../types.js';

interface HimalayasJob {
  title: string;
  companyName: string;
  companySlug: string;
  applicationLink?: string;
  guid: string;
}

interface HimalayasResponse {
  totalCount: number;
  offset: number;
  limit: number;
  jobs: HimalayasJob[];
}

export async function discoverFromHimalayas(maxPages = 5100): Promise<CompanyDiscovery[]> {
  const now = new Date().toISOString();

  // First request to get total count
  const first = await fetchJson<HimalayasResponse>('https://himalayas.app/jobs/api?limit=20&offset=0');
  if (!first?.jobs?.length) {
    console.log('Himalayas: failed to fetch');
    return [];
  }

  const totalJobs = first.totalCount;
  const perPage = 20;
  const totalPages = Math.min(Math.ceil(totalJobs / perPage), maxPages);
  console.log(`Himalayas: ${totalJobs.toLocaleString()} total jobs, fetching ${totalPages} pages...`);

  // Build list of offsets to fetch
  const offsets: number[] = [];
  for (let i = 0; i < totalPages; i++) {
    offsets.push(i * perPage);
  }

  // Aggregate companies from all fetched pages
  const companyCounts = new Map<string, { name: string; slug: string; count: number }>();

  // Process first page
  for (const job of first.jobs) {
    if (!job.companyName) continue;
    const key = job.companyName.toLowerCase();
    const existing = companyCounts.get(key);
    if (existing) existing.count++;
    else companyCounts.set(key, { name: job.companyName, slug: job.companySlug || '', count: 1 });
  }

  // Fetch remaining pages — use low concurrency to avoid rate limits
  let fetchedPages = 1;
  let nullResponses = 0;
  const remainingOffsets = offsets.slice(1);
  const BATCH_SIZE = 5;

  for (let i = 0; i < remainingOffsets.length; i += BATCH_SIZE) {
    const batch = remainingOffsets.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(
      batch.map(offset =>
        fetchJson<HimalayasResponse>(`https://himalayas.app/jobs/api?limit=20&offset=${offset}`)
      ),
    );

    for (const data of results) {
      if (!data?.jobs) { nullResponses++; continue; }
      for (const job of data.jobs) {
        if (!job.companyName) continue;
        const key = job.companyName.toLowerCase();
        const existing = companyCounts.get(key);
        if (existing) existing.count++;
        else companyCounts.set(key, { name: job.companyName, slug: job.companySlug || '', count: 1 });
      }
    }

    fetchedPages += batch.length;
    if (fetchedPages % 500 === 0) {
      const jobsSoFar = Array.from(companyCounts.values()).reduce((s, c) => s + c.count, 0);
      console.log(`Himalayas: ${fetchedPages}/${totalPages} pages, ${companyCounts.size} companies, ${jobsSoFar.toLocaleString()} jobs (${nullResponses} failed)`);
    }

    await sleep(200);
  }

  // Convert to results
  const results: CompanyDiscovery[] = [];
  for (const [, { name, slug, count }] of companyCounts) {
    results.push({
      company_name: name,
      job_board_url: slug
        ? `https://himalayas.app/companies/${slug}`
        : `https://himalayas.app/companies/${encodeURIComponent(name.toLowerCase().replace(/\s+/g, '-'))}`,
      estimated_jobs: count,
      source: 'himalayas',
      discovered_at: now,
    });
  }

  results.sort((a, b) => b.estimated_jobs - a.estimated_jobs);

  const total = results.reduce((s, c) => s + c.estimated_jobs, 0);
  console.log(`Himalayas: ${results.length} companies, ${total.toLocaleString()} total jobs (from ${fetchedPages} pages)`);

  return results;
}
