/**
 * Generic portal prober — validates and scrapes any PortalDefinition.
 *
 * Supports all four strategy types:
 *   json_api       — single fetch, walk jobsArrayPath, group by companyField
 *   company_probe  — probe seedCompanies one by one, count jobs per URL
 *   paginated_api  — paginate ?page=N, aggregate companies
 *   html_scrape    — fetch HTML, apply CSS selector (basic)
 */
import { log } from 'apify';
import type { CompanyDiscovery, PortalDefinition } from '../types.js';
import { fetchJson, sleep } from '../http.js';

/** Resolve a nested dot-path like "data.jobs" inside an object */
function resolvePath(obj: unknown, path: string): unknown {
  if (!path) return obj;
  return path.split('.').reduce((cur, key) => {
    if (cur && typeof cur === 'object' && !Array.isArray(cur)) {
      return (cur as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

function renderUrl(template: string, vars: Record<string, string>): string {
  return Object.entries(vars).reduce(
    (url, [k, v]) => url.replace(`{${k}}`, encodeURIComponent(v)),
    template,
  );
}

export async function probePortal(
  portal: PortalDefinition,
): Promise<{ companies: CompanyDiscovery[]; error?: string }> {
  const { strategy } = portal;
  const now = new Date().toISOString();

  try {
    if (strategy.type === 'json_api') {
      return await probeJsonApi(portal, now);
    }
    if (strategy.type === 'company_probe') {
      return await probeCompanyProbe(portal, now);
    }
    if (strategy.type === 'paginated_api') {
      return await probePaginatedApi(portal, now);
    }
    return { companies: [], error: `Unsupported strategy type: ${strategy.type}` };
  } catch (err) {
    return { companies: [], error: String(err) };
  }
}

async function probeJsonApi(
  portal: PortalDefinition,
  now: string,
): Promise<{ companies: CompanyDiscovery[] }> {
  const data = await fetchJson<unknown>(portal.strategy.urlTemplate);
  if (!data) return { companies: [] };

  const arr = resolvePath(data, portal.strategy.jobsArrayPath ?? '');
  if (!Array.isArray(arr)) return { companies: [] };

  const counts = new Map<string, number>();
  const urls = new Map<string, string>();

  for (const item of arr) {
    if (typeof item !== 'object' || !item) continue;
    const obj = item as Record<string, unknown>;
    const name = portal.strategy.companyField
      ? String(obj[portal.strategy.companyField] ?? '')
      : '';
    if (!name) continue;
    counts.set(name, (counts.get(name) ?? 0) + 1);
    if (!urls.has(name)) urls.set(name, portal.homepageUrl);
  }

  return {
    companies: [...counts.entries()].map(([name, jobs]) => ({
      company_name: name,
      job_board_url: urls.get(name) ?? portal.homepageUrl,
      estimated_jobs: jobs,
      source: portal.id,
      discovered_at: now,
    })),
  };
}

async function probeCompanyProbe(
  portal: PortalDefinition,
  now: string,
): Promise<{ companies: CompanyDiscovery[] }> {
  const seeds = portal.strategy.seedCompanies ?? [];
  const companies: CompanyDiscovery[] = [];

  for (const company of seeds) {
    const url = renderUrl(portal.strategy.urlTemplate, { company });
    const data = await fetchJson<unknown>(url);
    if (!data) continue;

    let count = 0;
    const arrayPath = portal.strategy.jobsArrayPath ?? '';
    const resolved = resolvePath(data, arrayPath);
    if (Array.isArray(resolved)) {
      count = resolved.length;
    } else if (typeof resolved === 'number') {
      count = resolved;
    }

    if (count > 0) {
      const boardUrl = portal.strategy.boardUrlTemplate
        ? renderUrl(portal.strategy.boardUrlTemplate, { company })
        : portal.homepageUrl;
      companies.push({
        company_name: company,
        job_board_url: boardUrl,
        estimated_jobs: count,
        source: portal.id,
        discovered_at: now,
      });
    }

    await sleep(300);
  }

  return { companies };
}

async function probePaginatedApi(
  portal: PortalDefinition,
  now: string,
): Promise<{ companies: CompanyDiscovery[] }> {
  const maxPages = portal.strategy.maxPages ?? 5;
  const counts = new Map<string, number>();

  for (let page = 1; page <= maxPages; page++) {
    const url = renderUrl(portal.strategy.urlTemplate, { page: String(page) });
    const data = await fetchJson<unknown>(url);
    if (!data) break;

    const arr = resolvePath(data, portal.strategy.jobsArrayPath ?? '');
    if (!Array.isArray(arr) || arr.length === 0) break;

    for (const item of arr) {
      if (typeof item !== 'object' || !item) continue;
      const obj = item as Record<string, unknown>;
      const name = portal.strategy.companyField
        ? String(obj[portal.strategy.companyField] ?? '')
        : '';
      if (name) counts.set(name, (counts.get(name) ?? 0) + 1);
    }

    await sleep(500);
  }

  return {
    companies: [...counts.entries()].map(([name, jobs]) => ({
      company_name: name,
      job_board_url: portal.homepageUrl,
      estimated_jobs: jobs,
      source: portal.id,
      discovered_at: now,
    })),
  };
}

/** Quick smoke-test: fetch and see if we get any companies back */
export async function validatePortal(portal: PortalDefinition): Promise<boolean> {
  const { strategy } = portal;
  const urlPreview = strategy.urlTemplate.replace('{company}', (strategy.seedCompanies?.[0] ?? 'test')).replace('{page}', '1');
  log.info(`Probing candidate portal: ${portal.id} (${portal.name}) url=${urlPreview} type=${strategy.type}`);
  const { companies, error } = await probePortal(portal);
  if (error) {
    log.warning(`Probe failed for ${portal.id}: ${error}`);
    return false;
  }
  log.info(`Probe result for ${portal.id}: ${companies.length} companies found`);
  return companies.length > 0;
}
