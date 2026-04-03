import type {
  SearchResponse as TsSearchResponse,
  SearchResponseHit,
  SearchResponseFacetCountSchema,
} from "typesense/lib/Typesense/Documents";
import { getSearchClient } from "./typesense-client";
import { buildFilterString, buildHistogramFilterString } from "./typesense-filters";
import type {
  PostingLocation,
  SearchFilters,
  SearchProvider,
  SearchResponse,
  SearchResultCompany,
  SearchResultPosting,
  HistogramFilters,
  SalaryBucket,
  ExperienceBucket,
} from "./types";

// ── Typesense document shapes ──────────────────────────────────────

interface JobPostingDoc {
  id: string;
  company_id: string;
  company_name: string;
  company_slug: string;
  company_icon?: string;
  title: string;
  is_active: boolean;
  location_ids: number[];
  location_names: string[];
  location_types: string[];
  location_geo_types: string[];
  occupation_id?: number;
  occupation_ids?: number[];
  seniority_id?: number;
  technology_ids: number[];
  employment_type?: string;
  salary_eur?: number;
  experience_min: number;
  locales: string[];
  first_seen_at: number;
  last_seen_at?: number;
}

interface CompanyDoc {
  id: string;
  name: string;
  slug: string;
  icon?: string;
  active_posting_count: number;
  year_posting_count: number;
}

type JobPostingHit = SearchResponseHit<JobPostingDoc>;
type GroupedHit = {
  group_key: string[];
  hits: JobPostingHit[];
  found?: number;
};
type FacetCount = SearchResponseFacetCountSchema<JobPostingDoc>;

// ── Helpers ────────────────────────────────────────────────────────

function emptyResponse(): SearchResponse {
  return { companies: [], totalCompanies: 0, degraded: true };
}

/**
 * Build PostingLocation[] from parallel arrays on a Typesense document.
 * Promotes filter-matching locations to the front of the list.
 */
function buildLocations(
  doc: JobPostingDoc,
  filteredLocationIds?: number[],
): PostingLocation[] {
  const locations = (doc.location_names ?? []).map(
    (name: string, i: number) => ({
      name,
      type: doc.location_types?.[i] ?? "onsite",
      geoType: (doc.location_geo_types?.[i] ?? undefined) as
        | "city"
        | "region"
        | "country"
        | "macro"
        | undefined,
      _locationId: doc.location_ids?.[i],
    }),
  );

  if (filteredLocationIds?.length) {
    const filterSet = new Set(filteredLocationIds);
    locations.sort((a, b) => {
      const aMatch = filterSet.has(a._locationId) ? 0 : 1;
      const bMatch = filterSet.has(b._locationId) ? 0 : 1;
      return aMatch - bMatch;
    });
  }

  return locations.map(({ _locationId: _, ...rest }) => rest);
}

/**
 * Map a single Typesense hit to a SearchResultPosting.
 */
function mapHitToPosting(
  hit: JobPostingHit,
  filteredLocationIds?: number[],
): SearchResultPosting {
  return {
    id: hit.document.id,
    title: hit.document.title || null,
    firstSeenAt: new Date(hit.document.first_seen_at * 1000),
    relevanceScore: hit.text_match,
    locations: buildLocations(hit.document, filteredLocationIds),
    isActive: hit.document.is_active,
  };
}

/**
 * Map grouped search results to SearchResponse.
 */
function mapGroupedHits(
  groupedHits: GroupedHit[],
  totalCompanies: number,
  yearCountMap: Map<string, number>,
  filteredLocationIds?: number[],
): SearchResponse {
  if (groupedHits.length === 0) {
    return { companies: [], totalCompanies };
  }

  const companies: SearchResultCompany[] = groupedHits.map(
    (group: GroupedHit) => {
      const firstHit = group.hits[0].document;
      const companyId = firstHit.company_id;
      return {
        company: {
          id: companyId,
          name: firstHit.company_name,
          slug: firstHit.company_slug,
          icon: firstHit.company_icon ?? null,
        },
        activeMatches: group.found ?? 0,
        yearMatches: yearCountMap.get(companyId) ?? 0,
        postings: group.hits.map((hit: JobPostingHit) =>
          mapHitToPosting(hit, filteredLocationIds),
        ),
      };
    },
  );

  return { companies, totalCompanies };
}

/**
 * Batch-fetch company docs for yearMatches.
 */
async function fetchYearCounts(
  companyIds: string[],
): Promise<Map<string, number>> {
  if (companyIds.length === 0) return new Map();

  const client = getSearchClient();
  const companyDocs: TsSearchResponse<CompanyDoc> = await client
    .collections<CompanyDoc>("company")
    .documents()
    .search({
      q: "*",
      filter_by: `id:[${companyIds.join(",")}]`,
      per_page: companyIds.length,
      include_fields: "id,year_posting_count",
    });

  const entries: [string, number][] = (companyDocs.hits ?? []).map(
    (h: SearchResponseHit<CompanyDoc>) =>
      [h.document.id, h.document.year_posting_count] as [string, number],
  );
  return new Map(entries);
}

function oneYearAgoUnix(): number {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return Math.floor(d.getTime() / 1000);
}

function isConnectionError(err: unknown): boolean {
  if (err instanceof Error) {
    const msg = err.message.toLowerCase();
    // Check message content
    if (
      msg.includes("econnrefused") ||
      msg.includes("econnreset") ||
      msg.includes("etimedout") ||
      msg.includes("socket hang up") ||
      msg.includes("request failed with status code 0") ||
      msg.includes("could not connect")
    ) {
      return true;
    }
    // AxiosError wraps the cause — check err.code (e.g. "ECONNREFUSED")
    const code = (err as { code?: string }).code;
    if (typeof code === "string") {
      const lc = code.toLowerCase();
      if (
        lc === "econnrefused" ||
        lc === "econnreset" ||
        lc === "etimedout" ||
        lc === "err_network"
      ) {
        return true;
      }
    }
  }
  return false;
}

// ── Salary histogram bucket definitions ────────────────────────────

const SALARY_FACET_RANGES = [
  ...Array.from({ length: 30 }, (_, i) => {
    const min = i * 10000;
    const max = (i + 1) * 10000;
    return `${min / 1000}-${max / 1000}k:[${min},${max}]`;
  }),
  "300k+:[300000,999999999]",
].join(", ");

const SALARY_FACET_BY = `salary_eur(${SALARY_FACET_RANGES})`;

// ── Provider ───────────────────────────────────────────────────────

export class TypesenseSearchProvider implements SearchProvider {
  async search(
    params: SearchFilters & {
      keywords: string[];
      offset: number;
      limit: number;
    },
  ): Promise<SearchResponse> {
    try {
      const { keywords, offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      const activeFilter = `is_active:true${filterStr ? " && " + filterStr : ""}`;
      const client = getSearchClient();

      // Main grouped search with facet for totalCompanies
      const result: TsSearchResponse<JobPostingDoc> = await client
        .collections<JobPostingDoc>("job_posting")
        .documents()
        .search({
          q: keywords.join(" "),
          query_by: "title",
          filter_by: activeFilter,
          sort_by: "_text_match:desc,first_seen_at:desc",
          group_by: "company_id",
          group_limit: 10,
          per_page: limit,
          page: Math.floor(offset / limit) + 1,
          typo_tokens_threshold: 1,
          drop_tokens_threshold: 1,
          facet_by: "company_id",
          facet_strategy: "exhaustive",
          max_facet_values: 1,
        });

      const totalCompanies =
        result.facet_counts?.[0]?.stats?.total_values ?? 0;

      // Batch-fetch company docs for yearMatches
      const groupedHits = (result.grouped_hits ?? []) as GroupedHit[];
      const companyIds = groupedHits.map(
        (g: GroupedHit) => g.hits[0].document.company_id,
      );
      const yearCountMap = await fetchYearCounts(companyIds);

      return mapGroupedHits(groupedHits, totalCompanies, yearCountMap, locationIds);
    } catch (err) {
      if (isConnectionError(err)) return emptyResponse();
      throw err;
    }
  }

  async listTopCompanies(
    params: SearchFilters & { offset: number; limit: number },
  ): Promise<SearchResponse> {
    try {
      const { offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      const hasFilters = filterStr.length > 0;

      if (!hasFilters) {
        // Unfiltered: query the company collection directly (pre-computed counts)
        return await this.listTopCompaniesUnfiltered(offset, limit);
      }

      // Filtered: facet-based approach
      return await this.listTopCompaniesFiltered(
        filterStr,
        offset,
        limit,
        locationIds,
      );
    } catch (err) {
      if (isConnectionError(err)) return emptyResponse();
      throw err;
    }
  }

  private async listTopCompaniesFiltered(
    filterStr: string,
    offset: number,
    limit: number,
    locationIds?: number[],
  ): Promise<SearchResponse> {
    const client = getSearchClient();
    const activeFilter = `is_active:true && ${filterStr}`;

    // Active count facet to rank companies
    const activeResult: TsSearchResponse<JobPostingDoc> = await client
      .collections<JobPostingDoc>("job_posting")
      .documents()
      .search({
        q: "*",
        filter_by: activeFilter,
        facet_by: "company_id",
        facet_strategy: "exhaustive",
        max_facet_values: offset + limit,
        per_page: 0,
      });

    const activeFacets: FacetCount | undefined = activeResult.facet_counts?.[0];
    const totalCompanies = activeFacets?.stats?.total_values ?? 0;
    const activeCounts = activeFacets?.counts ?? [];

    const page = activeCounts.slice(offset, offset + limit);
    if (page.length === 0) {
      return { companies: [], totalCompanies };
    }

    const companyIds = page.map(
      (f: { value: string; count: number }) => f.value,
    );
    const activeCountMap = new Map(
      page.map(
        (f: { value: string; count: number }) =>
          [f.value, f.count] as [string, number],
      ),
    );
    // Fetch year counts from company collection (pre-computed) — more reliable
    // than a year facet which may not return all companies on the current page
    const yearCountMap = await fetchYearCounts(companyIds);

    // Fetch postings for this page of companies
    const postingResults: TsSearchResponse<JobPostingDoc> = await client
      .collections<JobPostingDoc>("job_posting")
      .documents()
      .search({
        q: "*",
        filter_by: `company_id:[${companyIds.join(",")}] && ${activeFilter}`,
        group_by: "company_id",
        group_limit: 10,
        sort_by: "first_seen_at:desc",
        per_page: companyIds.length,
      });

    // Build a map of company_id -> group for ordered assembly
    const groupedHits = (postingResults.grouped_hits ?? []) as GroupedHit[];
    const groupMap = new Map<string, GroupedHit>(
      groupedHits.map(
        (g: GroupedHit) =>
          [g.hits[0].document.company_id, g] as [string, GroupedHit],
      ),
    );

    const companies: SearchResultCompany[] = companyIds
      .map((companyId: string) => {
        const group = groupMap.get(companyId);
        if (!group) return null;
        const firstHit = group.hits[0].document;
        return {
          company: {
            id: companyId,
            name: firstHit.company_name,
            slug: firstHit.company_slug,
            icon: firstHit.company_icon ?? null,
          },
          activeMatches: activeCountMap.get(companyId) ?? 0,
          yearMatches: yearCountMap.get(companyId) ?? 0,
          postings: group.hits.map((hit: JobPostingHit) =>
            mapHitToPosting(hit, locationIds),
          ),
        };
      })
      .filter((c): c is SearchResultCompany => c !== null);

    return { companies, totalCompanies };
  }

  private async listTopCompaniesUnfiltered(
    offset: number,
    limit: number,
  ): Promise<SearchResponse> {
    const client = getSearchClient();

    // Query company collection sorted by active_posting_count
    const companyResults: TsSearchResponse<CompanyDoc> = await client
      .collections<CompanyDoc>("company")
      .documents()
      .search({
        q: "*",
        filter_by: "active_posting_count:>0",
        sort_by: "active_posting_count:desc",
        per_page: limit,
        page: Math.floor(offset / limit) + 1,
      });

    const totalCompanies = companyResults.found;
    const companyHits = companyResults.hits ?? [];
    if (companyHits.length === 0) {
      return { companies: [], totalCompanies };
    }

    const companyIds = companyHits.map(
      (h: SearchResponseHit<CompanyDoc>) => h.document.id,
    );
    const companyMap = new Map<string, CompanyDoc>(
      companyHits.map(
        (h: SearchResponseHit<CompanyDoc>) =>
          [h.document.id, h.document] as [string, CompanyDoc],
      ),
    );

    // Fetch postings for these companies
    const postingResults: TsSearchResponse<JobPostingDoc> = await client
      .collections<JobPostingDoc>("job_posting")
      .documents()
      .search({
        q: "*",
        filter_by: `company_id:[${companyIds.join(",")}] && is_active:true`,
        group_by: "company_id",
        group_limit: 10,
        sort_by: "first_seen_at:desc",
        per_page: companyIds.length,
      });

    const groupedHits = (postingResults.grouped_hits ?? []) as GroupedHit[];
    const groupMap = new Map<string, GroupedHit>(
      groupedHits.map(
        (g: GroupedHit) =>
          [g.hits[0].document.company_id, g] as [string, GroupedHit],
      ),
    );

    const companies: SearchResultCompany[] = companyIds
      .map((companyId: string) => {
        const compDoc = companyMap.get(companyId);
        const group = groupMap.get(companyId);
        if (!compDoc) return null;
        return {
          company: {
            id: companyId,
            name: compDoc.name,
            slug: compDoc.slug,
            icon: compDoc.icon ?? null,
          },
          activeMatches: compDoc.active_posting_count,
          yearMatches: compDoc.year_posting_count,
          postings: group
            ? group.hits.map((hit: JobPostingHit) => mapHitToPosting(hit))
            : [],
        };
      })
      .filter((c): c is SearchResultCompany => c !== null);

    return { companies, totalCompanies };
  }

  async loadPostings(
    params: SearchFilters & {
      companyId: string;
      keywords: string[];
      offset: number;
      limit: number;
    },
  ): Promise<SearchResultPosting[]> {
    try {
      const { companyId, keywords, offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      const activeFilter = `company_id:=${companyId} && is_active:true${filterStr ? " && " + filterStr : ""}`;
      const q = keywords.length ? keywords.join(" ") : "*";
      const client = getSearchClient();

      const result: TsSearchResponse<JobPostingDoc> = await client
        .collections<JobPostingDoc>("job_posting")
        .documents()
        .search({
          q,
          query_by: "title",
          filter_by: activeFilter,
          sort_by: keywords.length
            ? "_text_match:desc,first_seen_at:desc"
            : "first_seen_at:desc",
          per_page: limit,
          page: Math.floor(offset / limit) + 1,
        });

      return (result.hits ?? []).map((hit: JobPostingHit) =>
        mapHitToPosting(hit, locationIds),
      );
    } catch (err) {
      if (isConnectionError(err)) return [];
      throw err;
    }
  }

  async loadPostingsWithCounts(
    params: SearchFilters & {
      companyId: string;
      keywords: string[];
      offset: number;
      limit: number;
    },
  ): Promise<{
    postings: SearchResultPosting[];
    activeCount: number;
    yearCount: number;
  }> {
    try {
      const { companyId, keywords, offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      const baseFilter = `company_id:=${companyId}${filterStr ? " && " + filterStr : ""}`;
      const q = keywords.length ? keywords.join(" ") : "*";
      const client = getSearchClient();

      // Three queries in parallel: postings + active count + year count
      const [postingsResult, activeResult, yearResult] = await Promise.all([
        client
          .collections<JobPostingDoc>("job_posting")
          .documents()
          .search({
            q,
            query_by: "title",
            filter_by: `is_active:true && ${baseFilter}`,
            sort_by: keywords.length
              ? "_text_match:desc,first_seen_at:desc"
              : "first_seen_at:desc",
            per_page: limit,
            page: Math.floor(offset / limit) + 1,
          }),
        client
          .collections<JobPostingDoc>("job_posting")
          .documents()
          .search({
            q,
            query_by: "title",
            filter_by: `is_active:true && ${baseFilter}`,
            per_page: 0,
          }),
        client
          .collections<JobPostingDoc>("job_posting")
          .documents()
          .search({
            q,
            query_by: "title",
            filter_by: `first_seen_at:>${oneYearAgoUnix()} && ${baseFilter}`,
            per_page: 0,
          }),
      ]);

      const postings = (postingsResult.hits ?? []).map(
        (hit: JobPostingHit) => mapHitToPosting(hit, locationIds),
      );
      const activeCount = activeResult.found;
      const yearCount = yearResult.found;

      return { postings, activeCount, yearCount };
    } catch (err) {
      if (isConnectionError(err)) {
        return { postings: [], activeCount: 0, yearCount: 0 };
      }
      throw err;
    }
  }

  async getSalaryHistogram(
    filters?: HistogramFilters,
  ): Promise<SalaryBucket[]> {
    try {
      const f = filters ?? {};
      const filterStr = buildHistogramFilterString(f);
      const hasKeywords = f.keywords && f.keywords.length > 0;
      const q = hasKeywords ? f.keywords!.join(" ") : "*";
      const client = getSearchClient();

      let filterBy = `is_active:true && salary_eur:>0${filterStr ? " && " + filterStr : ""}`;
      if (f.companyId) {
        filterBy += ` && company_id:=${f.companyId}`;
      }

      const result: TsSearchResponse<JobPostingDoc> = await client
        .collections<JobPostingDoc>("job_posting")
        .documents()
        .search({
          q,
          query_by: "title",
          filter_by: filterBy,
          facet_by: SALARY_FACET_BY,
          max_facet_values: 31,
          per_page: 0,
        });

      const facet = result.facet_counts?.[0];
      if (!facet) return [];

      const buckets: SalaryBucket[] = [];
      for (const entry of facet.counts) {
        const range = parseFacetRange(entry.value);
        if (range) {
          buckets.push({ min: range[0], max: range[1], count: entry.count });
        }
      }
      buckets.sort((a: SalaryBucket, b: SalaryBucket) => a.min - b.min);
      return buckets;
    } catch (err) {
      if (isConnectionError(err)) return [];
      throw err;
    }
  }

  async getExperienceHistogram(
    filters?: HistogramFilters,
  ): Promise<ExperienceBucket[]> {
    try {
      const f = filters ?? {};
      const filterStr = buildHistogramFilterString(f);
      const hasKeywords = f.keywords && f.keywords.length > 0;
      const q = hasKeywords ? f.keywords!.join(" ") : "*";
      const client = getSearchClient();

      let filterBy = `is_active:true && experience_min:>=0${filterStr ? " && " + filterStr : ""}`;
      if (f.companyId) {
        filterBy += ` && company_id:=${f.companyId}`;
      }

      const result: TsSearchResponse<JobPostingDoc> = await client
        .collections<JobPostingDoc>("job_posting")
        .documents()
        .search({
          q,
          query_by: "title",
          filter_by: filterBy,
          facet_by: "experience_min",
          max_facet_values: 30,
          per_page: 0,
        });

      const facet = result.facet_counts?.[0];
      if (!facet) return [];

      const buckets: ExperienceBucket[] = [];
      for (const entry of facet.counts) {
        const years = parseInt(entry.value, 10);
        if (!isNaN(years)) {
          buckets.push({ years, count: entry.count });
        }
      }
      buckets.sort(
        (a: ExperienceBucket, b: ExperienceBucket) => a.years - b.years,
      );
      return buckets;
    } catch (err) {
      if (isConnectionError(err)) return [];
      throw err;
    }
  }
}

// ── Facet range parser ─────────────────────────────────────────────

/**
 * Parse a facet range label like "0-10k" or "300k+" back to [min, max].
 * The label format corresponds to what we pass to Typesense facet_by ranges.
 */
function parseFacetRange(label: string): [number, number] | null {
  // Handle overflow bucket "300k+"
  if (label.endsWith("+")) {
    const num = parseFloat(label.replace("k+", "")) * 1000;
    if (isNaN(num)) return null;
    return [num, 999999999];
  }

  // Handle standard "X-Yk" labels like "0-10k", "10-20k"
  const match = label.match(/^(\d+)-(\d+)k$/);
  if (match) {
    return [parseInt(match[1], 10) * 1000, parseInt(match[2], 10) * 1000];
  }

  return null;
}
