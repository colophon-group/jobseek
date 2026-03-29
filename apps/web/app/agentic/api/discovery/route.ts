/**
 * GET /agentic/api/discovery
 *
 * Returns the current portal registry from the company-discovery-actor's
 * latest dataset run — showing which job portals are known, active,
 * AI-suggested, and how many companies each found.
 *
 * No auth required (read-only public data).
 */
import { type NextRequest, NextResponse } from 'next/server';

const APIFY_TOKEN = process.env.APIFY_TOKEN;
const ACTOR_ID = process.env.APIFY_DISCOVERY_ACTOR_ID ?? 'golanger/company-discovery-actor';

export async function GET(_req: NextRequest) {
  if (!APIFY_TOKEN) {
    return NextResponse.json({ error: 'APIFY_TOKEN not configured' }, { status: 503 });
  }

  // Fetch the latest dataset from the actor's last run
  const runsRes = await fetch(
    `https://api.apify.com/v2/acts/${encodeURIComponent(ACTOR_ID)}/runs?token=${APIFY_TOKEN}&limit=1&desc=1`,
    { next: { revalidate: 300 } },
  );

  if (!runsRes.ok) {
    return NextResponse.json({ error: 'Failed to fetch runs from Apify' }, { status: 502 });
  }

  const runs = await runsRes.json();
  const latestRun = runs?.data?.items?.[0];

  if (!latestRun) {
    return NextResponse.json({ error: 'No runs found for company-discovery-actor' }, { status: 404 });
  }

  // Fetch dataset items — look for the registry_summary record
  const datasetRes = await fetch(
    `https://api.apify.com/v2/datasets/${latestRun.defaultDatasetId}/items?token=${APIFY_TOKEN}&limit=2000`,
    { next: { revalidate: 300 } },
  );

  if (!datasetRes.ok) {
    return NextResponse.json({ error: 'Failed to fetch dataset' }, { status: 502 });
  }

  const items = await datasetRes.json() as Record<string, unknown>[];
  const summary = items.find(i => i._type === 'registry_summary');
  const companies = items.filter(i => !i._type);

  // Top 20 companies by job count
  const topCompanies = [...companies]
    .sort((a, b) => ((b.estimated_jobs as number) ?? 0) - ((a.estimated_jobs as number) ?? 0))
    .slice(0, 20)
    .map((c) => ({
      name: c.company_name,
      jobs: c.estimated_jobs,
      source: c.source,
      url: c.job_board_url,
    }));

  // Hiring.cafe: top 10 fastest-growing companies (largest positive delta)
  const growingCompanies = companies
    .filter((c) => c.source === 'hiring-cafe' && typeof c.jobs_delta === 'number' && (c.jobs_delta as number) > 0)
    .sort((a, b) => ((b.jobs_delta as number) ?? 0) - ((a.jobs_delta as number) ?? 0))
    .slice(0, 10)
    .map((c) => ({
      name: c.company_name,
      jobs: c.estimated_jobs,
      prevJobs: c.prev_jobs,
      delta: c.jobs_delta,
      url: c.job_board_url,
    }));

  return NextResponse.json({
    runId: latestRun.id,
    runAt: latestRun.startedAt,
    runStatus: latestRun.status,
    companiesDiscovered: companies.length,
    registry: summary ?? null,
    topCompanies,
    // Companies with the largest growth in hiring.cafe job count since last run
    growingCompanies,
  });
}
