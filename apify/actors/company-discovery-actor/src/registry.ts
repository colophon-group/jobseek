/**
 * Portal registry — persisted in Apify KV store "company-discovery-portals".
 *
 * On every run the actor:
 *   1. Loads the registry (or seeds with hardcoded portals on first run)
 *   2. Runs known active portals
 *   3. Asks Gemini to suggest new ones
 *   4. Probes candidates and promotes to active/failed
 *   5. Saves the updated registry back to KV store
 */
import { Actor, log } from 'apify';
import type { PortalDefinition, PortalRegistry } from './types.js';

const KV_STORE_NAME = 'company-discovery-portals';
const KV_KEY = 'registry';

/** The five hardcoded sources that always exist */
const SEED_PORTALS: PortalDefinition[] = [
  {
    id: 'greenhouse',
    name: 'Greenhouse ATS',
    description: 'Public Greenhouse job boards probed via boards-api.greenhouse.io',
    homepageUrl: 'https://www.greenhouse.com',
    strategy: { type: 'company_probe', urlTemplate: 'https://boards-api.greenhouse.io/v1/boards/{company}/jobs', boardUrlTemplate: 'https://boards.greenhouse.io/{company}' },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
  {
    id: 'themuse',
    name: 'The Muse',
    description: 'Company directory with job listings via api.themuse.com',
    homepageUrl: 'https://www.themuse.com',
    strategy: { type: 'paginated_api', urlTemplate: 'https://www.themuse.com/api/public/jobs?page={page}', jobsArrayPath: 'results', companyField: 'company.name', maxPages: 100 },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
  {
    id: 'arbeitnow',
    name: 'Arbeitnow',
    description: 'EU/remote job aggregator with public API',
    homepageUrl: 'https://www.arbeitnow.com',
    strategy: { type: 'paginated_api', urlTemplate: 'https://www.arbeitnow.com/api/job-board-api?page={page}', jobsArrayPath: 'data', companyField: 'company_name', maxPages: 50 },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
  {
    id: 'remotive',
    name: 'Remotive',
    description: 'Remote job aggregator with public API',
    homepageUrl: 'https://remotive.com',
    strategy: { type: 'json_api', urlTemplate: 'https://remotive.com/api/remote-jobs', jobsArrayPath: 'jobs', companyField: 'company_name' },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
  {
    id: 'megaemployers',
    name: 'Mega Employers',
    description: 'Curated list of 150+ global giants',
    homepageUrl: 'https://jobseek.colophon-group.org',
    strategy: { type: 'json_api', urlTemplate: 'internal' },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
  {
    id: 'hiring-cafe',
    name: 'Hiring.cafe',
    description: 'Job board with per-posting engagement metrics. Paginated POST API, no auth. Job counts per company memorised in KV store for delta tracking across runs.',
    homepageUrl: 'https://hiring.cafe',
    strategy: { type: 'paginated_api', urlTemplate: 'https://hiring.cafe/api/search-jobs', maxPages: 20 },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
  {
    id: 'smartrecruiters',
    name: 'SmartRecruiters ATS',
    description: 'Enterprise ATS used by Fortune 500 companies. Public per-company jobs API at api.smartrecruiters.com/v1/companies/{company}/postings.',
    homepageUrl: 'https://www.smartrecruiters.com',
    strategy: {
      type: 'company_probe',
      urlTemplate: 'https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=200',
      seedCompanies: [
        'BoschGroup', 'SephoraUSA', 'IKEA', 'McDonalds', 'Twitter',
        'Lidl', 'Aldi', 'Carrefour', 'Vodafone', 'BNPParibas',
        'Siemens', 'BASF', 'Henkel', 'SAP', 'Zalando',
      ],
      jobsArrayPath: 'content',
      boardUrlTemplate: 'https://jobs.smartrecruiters.com/{company}',
    },
    status: 'active',
    suggestedBy: 'hardcoded',
    discoveredAt: new Date().toISOString(),
  },
];

export async function loadRegistry(): Promise<PortalRegistry> {
  const store = await Actor.openKeyValueStore(KV_STORE_NAME);
  const existing = await store.getValue<PortalRegistry>(KV_KEY);

  if (existing) {
    log.info(`Registry loaded: ${existing.portals.length} portals (updated ${existing.updatedAt})`);
    // Merge in any seed portals not yet present
    for (const seed of SEED_PORTALS) {
      if (!existing.portals.find(p => p.id === seed.id)) {
        existing.portals.push(seed);
        log.info(`Seeded missing portal: ${seed.id}`);
      }
    }
    return existing;
  }

  log.info('No registry found — seeding with hardcoded portals');
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    portals: [...SEED_PORTALS],
  };
}

export async function saveRegistry(registry: PortalRegistry): Promise<void> {
  registry.updatedAt = new Date().toISOString();
  const store = await Actor.openKeyValueStore(KV_STORE_NAME);
  await store.setValue(KV_KEY, registry);
  log.info(`Registry saved: ${registry.portals.length} portals`);
}

export function getActivePortals(registry: PortalRegistry): PortalDefinition[] {
  return registry.portals.filter(p => p.status === 'active');
}

export function getCandidatePortals(registry: PortalRegistry): PortalDefinition[] {
  return registry.portals.filter(p => p.status === 'candidate');
}

export function upsertPortal(registry: PortalRegistry, portal: PortalDefinition): void {
  const idx = registry.portals.findIndex(p => p.id === portal.id);
  if (idx >= 0) {
    registry.portals[idx] = portal;
  } else {
    registry.portals.push(portal);
  }
}
