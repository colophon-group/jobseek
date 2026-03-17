export interface PostingLocation {
  name: string;
  type: string;
  geoType?: "city" | "region" | "country" | "macro";
}

export interface SearchResultPosting {
  id: string;
  title: string | null;
  firstSeenAt: Date | string;
  relevanceScore: number;
  locations: PostingLocation[];
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
}

export interface SearchFilters {
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
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
