export interface PostingLocation {
  name: string;
  type: string;
  geoType?: "city" | "region" | "country" | "macro";
}

/**
 * Work-mode (location_types) filter values. Source of truth lives in the
 * crawler's ``enum_normalize.py`` (canonical: onsite | remote | hybrid).
 * Issue #2983 — filter-wide multi-select reusing the existing
 * ``location_types`` field on ``job_posting``. No schema change.
 */
export type WorkMode = "onsite" | "hybrid" | "remote";

export const WORK_MODE_VALUES: readonly WorkMode[] = ["onsite", "hybrid", "remote"] as const;

export function isWorkMode(value: string): value is WorkMode {
  return (WORK_MODE_VALUES as readonly string[]).includes(value);
}

export interface SearchResultPosting {
  id: string;
  title: string | null;
  firstSeenAt: Date | string;
  relevanceScore: number;
  locations: PostingLocation[];
  isActive?: boolean;
}

export interface SearchResultCompany {
  company: { id: string; name: string; slug: string; icon: string | null };
  activeMatches: number;
  yearMatches: number;
  postings: SearchResultPosting[];
}

export interface SearchResponse {
  companies: SearchResultCompany[];
  totalCompanies: number;
  truncated?: boolean;
  degraded?: boolean;
}

export interface SearchFilters {
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  workMode?: WorkMode[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages: string[];
  locale: string;
}

export interface HistogramFilters {
  companyId?: string;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  workMode?: WorkMode[];
  /**
   * Cross-filter context for the employment-type modal facet counts.
   * Added in #3032 so toggling work-mode/occupation/etc. live-updates
   * employment-type counts in the same way that location/level already
   * cross-filter into the seniority and technology modals.
   */
  employmentTypes?: string[];
  languages?: string[];
}

export interface SalaryBucket {
  min: number;
  max: number;
  count: number;
}

export interface ExperienceBucket {
  years: number;
  count: number;
}

export interface SearchProvider {
  search(params: SearchFilters & {
    keywords: string[];
    offset: number;
    limit: number;
  }): Promise<SearchResponse>;

  listTopCompanies(params: SearchFilters & {
    offset: number;
    limit: number;
  }): Promise<SearchResponse>;

  loadPostings(params: SearchFilters & {
    companyId: string;
    keywords: string[];
    offset: number;
    limit: number;
  }): Promise<SearchResultPosting[]>;

  loadPostingsWithCounts(params: SearchFilters & {
    companyId: string;
    keywords: string[];
    offset: number;
    limit: number;
  }): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number }>;

  getSalaryHistogram(filters?: HistogramFilters): Promise<SalaryBucket[]>;

  getExperienceHistogram(filters?: HistogramFilters): Promise<ExperienceBucket[]>;
}
