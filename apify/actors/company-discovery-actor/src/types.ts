export interface CompanyDiscovery {
  company_name: string;
  job_board_url: string;
  estimated_jobs: number;
  source: string;
  discovered_at: string;
  // Populated by sources that track history across runs (e.g. hiring-cafe)
  prev_jobs?: number | null;    // job count from the previous run
  jobs_delta?: number | null;   // change since last run (+growing, -shrinking)
}

/** Scraping strategy for a generic portal */
export type PortalStrategyType =
  | 'json_api'        // GET url returns JSON array/object with company+job data
  | 'company_probe'   // probe per-company URLs from a seed list
  | 'paginated_api'   // paginate through ?page=N until empty
  | 'html_scrape';    // scrape HTML listing

export interface PortalStrategy {
  type: PortalStrategyType;
  /** URL to fetch (supports {company} and {page} placeholders) */
  urlTemplate: string;
  /** Seed companies to try for company_probe */
  seedCompanies?: string[];
  /** JSON path expression to the array of job listings (e.g. "jobs", "data.jobs") */
  jobsArrayPath?: string;
  /** Field inside each job item that holds the company name */
  companyField?: string;
  /** Field inside each job item that holds the job count (or omit to count array length) */
  countField?: string;
  /** CSS selector for company name when type=html_scrape */
  companyCssSelector?: string;
  /** Max pages to fetch for paginated_api */
  maxPages?: number;
  /** User-facing board URL template for company_probe (supports {company} placeholder) */
  boardUrlTemplate?: string;
}

export type PortalStatus = 'active' | 'candidate' | 'probing' | 'failed' | 'disabled';

export interface PortalDefinition {
  id: string;
  name: string;
  description: string;
  homepageUrl: string;
  strategy: PortalStrategy;
  status: PortalStatus;
  suggestedBy: 'hardcoded' | 'gemini';
  geminiReasoning?: string;
  discoveredAt: string;
  lastProbedAt?: string;
  lastSuccessAt?: string;
  companiesFound?: number;
  probeError?: string;
}

/** Persisted registry stored in Apify KV store */
export interface PortalRegistry {
  version: number;
  updatedAt: string;
  portals: PortalDefinition[];
}
