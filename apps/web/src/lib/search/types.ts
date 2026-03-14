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

export interface SearchProvider {
  search(params: {
    keywords: string[];
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<SearchResponse>;

  listTopCompanies(params: {
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<SearchResponse>;

  loadPostings(params: {
    companyId: string;
    keywords: string[];
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<SearchResultPosting[]>;

  loadPostingsWithCounts(params: {
    companyId: string;
    keywords: string[];
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number }>;
}
