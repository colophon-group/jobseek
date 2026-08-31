/**
 * Persisted structured filters shared by interactive watchlists and headless
 * candidate consumers. The JSONB value is untrusted at read time; the
 * canonical compiler in `services/watchlist-matcher.ts` validates values
 * before they reach Typesense.
 */
export type WatchlistFilters = {
  keywords?: string[];
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  workMode?: ("onsite" | "hybrid" | "remote")[];
  employmentType?: string[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  anyCompany?: boolean;
};

/** Fully resolved filter state consumed by the canonical candidate reader. */
export type WatchlistCandidateFilters = {
  /** Candidate readers treat company membership as immutable input. */
  companyIds: readonly string[];
  anyCompany?: boolean;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  workMode?: ("onsite" | "hybrid" | "remote")[];
  employmentType?: string[];
  /** EUR-equivalent thresholds, despite the legacy property names. */
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  /** Effective job-language preference. Empty means all languages. */
  languages?: string[];
};

export type WatchlistPostingEntry = {
  id: string;
  title: string | null;
  /** Leaf location names, in source order, used to disambiguate repeated titles. */
  locationNames?: string[];
  sourceUrl: string;
  firstSeenAt: string;
  isActive: boolean;
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
};

/**
 * Session-free input to the persisted-filter compiler. Background callers
 * batch-load these fields; interactive callers build the same shape from the
 * already-authorized watchlist page row.
 */
export type WatchlistMatcherSource = {
  watchlistId: string;
  watchlistLabel: string;
  filters: WatchlistFilters | null;
  companyIds: string[];
  locale: string;
  jobLanguages: string[];
};

export type CompiledWatchlistMatcher = {
  watchlistId: string;
  watchlistLabel: string;
  candidateFilters: WatchlistCandidateFilters;
};

export type MatchedWatchlistLabel = {
  id: string;
  label: string;
};

export type MatchedWatchlistPosting = WatchlistPostingEntry & {
  matchedWatchlists: MatchedWatchlistLabel[];
};
