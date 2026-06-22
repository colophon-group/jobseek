import type {
  SearchResponse as TsSearchResponse,
  SearchResponseHit,
  SearchResponseFacetCountSchema,
} from "typesense/lib/Typesense/Documents";
import { getSearchClient } from "./typesense-client";
import { buildFilterString, POSTING_BASE_FILTER, POSTING_FLOW_FILTER } from "./typesense-filters";
import { withTypesenseRetry } from "./typesense-retry";
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

function mapHitsToPostingsByFreshness(
  hits: JobPostingHit[],
  filteredLocationIds?: number[],
): SearchResultPosting[] {
  if (hits.length <= 1) {
    return hits.map((hit) => mapHitToPosting(hit, filteredLocationIds));
  }

  return [...hits]
    .sort((a, b) => b.document.first_seen_at - a.document.first_seen_at)
    .map((hit) => mapHitToPosting(hit, filteredLocationIds));
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
 * Compute filtered year counts via facet on job_posting.
 * Counts postings from the past year matching the same filters as the main query.
 */
async function fetchYearCountsFiltered(
  companyIds: string[],
  filterStr: string,
  q: string,
): Promise<Map<string, number>> {
  if (companyIds.length === 0) return new Map();

  const client = getSearchClient();
  const yearFilter = `${POSTING_FLOW_FILTER} && first_seen_at:>${oneYearAgoUnix()} && company_id:[${companyIds.join(",")}]${filterStr ? " && " + filterStr : ""}`;

  const result: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
    () =>
      client
        .collections<JobPostingDoc>("job_posting")
        .documents()
        .search({
          q,
          query_by: "title",
          filter_by: yearFilter,
          facet_by: "company_id",
          facet_strategy: "exhaustive",
          max_facet_values: companyIds.length,
          per_page: 0,
        }),
    { label: "yearCountsFiltered" },
  );

  const counts = result.facet_counts?.[0]?.counts ?? [];
  return new Map(
    counts.map((c: { value: string; count: number }) => [c.value, c.count]),
  );
}

function oneYearAgoUnix(): number {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return Math.floor(d.getTime() / 1000);
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
      const activeFilter = `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`;
      const client = getSearchClient();

      // Main grouped search with facet for totalCompanies
      const result: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
        () =>
          client
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
            }),
        { label: "search" },
      );

      const totalCompanies =
        result.facet_counts?.[0]?.stats?.total_values ?? 0;

      // Compute filtered year counts — same keywords + filters but for the past year
      const groupedHits = (result.grouped_hits ?? []) as GroupedHit[];
      const companyIds = groupedHits.map(
        (g: GroupedHit) => g.hits[0].document.company_id,
      );
      const yearCountMap = await fetchYearCountsFiltered(
        companyIds,
        filterStr,
        keywords.join(" "),
      );

      return mapGroupedHits(groupedHits, totalCompanies, yearCountMap, locationIds);
    } catch (err) {
      console.error("[typesense] search error", err);
      return emptyResponse();
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
      console.error("[typesense] search error", err);
      return emptyResponse();
    }
  }

  private async listTopCompaniesFiltered(
    filterStr: string,
    offset: number,
    limit: number,
    locationIds?: number[],
  ): Promise<SearchResponse> {
    const client = getSearchClient();
    const activeFilter = `${POSTING_BASE_FILTER} && ${filterStr}`;

    // Active count facet to rank companies
    const activeResult: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
      () =>
        client
          .collections<JobPostingDoc>("job_posting")
          .documents()
          .search({
            q: "*",
            filter_by: activeFilter,
            facet_by: "company_id",
            facet_strategy: "exhaustive",
            max_facet_values: offset + limit,
            per_page: 0,
          }),
      { label: "topCompaniesActiveCount" },
    );

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
    // Fetch filtered year counts and postings in parallel (independent queries)
    const [yearCountMap, postingResults] = await Promise.all([
      fetchYearCountsFiltered(companyIds, filterStr, "*"),
      withTypesenseRetry(
        () =>
          client
            .collections<JobPostingDoc>("job_posting")
            .documents()
            .search({
              q: "*",
              filter_by: `company_id:[${companyIds.join(",")}] && ${activeFilter}`,
              group_by: "company_id",
              group_limit: 10,
              sort_by: "first_seen_at:desc",
              per_page: companyIds.length,
            }),
        { label: "topCompaniesFilteredPostings" },
      ),
    ]);

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
          postings: mapHitsToPostingsByFreshness(group.hits, locationIds),
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

    const postingResults: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
      () =>
        client
          .collections<JobPostingDoc>("job_posting")
          .documents()
          .search({
            q: "*",
            filter_by: POSTING_BASE_FILTER,
            group_by: "company_id",
            group_limit: 10,
            sort_by: "first_seen_at:desc",
            per_page: limit,
            page: Math.floor(offset / limit) + 1,
            facet_by: "company_id",
            facet_strategy: "exhaustive",
            max_facet_values: 1,
          }),
      { label: "topCompaniesUnfiltered" },
    );

    const groupedHits = (postingResults.grouped_hits ?? []) as GroupedHit[];
    const totalCompanies =
      postingResults.facet_counts?.[0]?.stats?.total_values ?? groupedHits.length;
    if (groupedHits.length === 0) {
      return { companies: [], totalCompanies };
    }

    const companyIds = groupedHits.map(
      (g: GroupedHit) => g.hits[0].document.company_id,
    );
    const companyResults: TsSearchResponse<CompanyDoc> = await withTypesenseRetry(
      () =>
        client
          .collections<CompanyDoc>("company")
          .documents()
          .search({
            q: "*",
            filter_by: `id:[${companyIds.join(",")}]`,
            per_page: companyIds.length,
          }),
      { label: "topCompaniesUnfilteredCompanies" },
    );

    const companyMap = new Map<string, CompanyDoc>(
      (companyResults.hits ?? []).map(
        (h: SearchResponseHit<CompanyDoc>) =>
          [h.document.id, h.document] as [string, CompanyDoc],
      ),
    );

    const companies: SearchResultCompany[] = groupedHits
      .map((group: GroupedHit) => {
        const firstHit = group.hits[0].document;
        const companyId = firstHit.company_id;
        const compDoc = companyMap.get(companyId);
        return {
          company: {
            id: companyId,
            name: compDoc?.name ?? firstHit.company_name,
            slug: compDoc?.slug ?? firstHit.company_slug,
            icon: compDoc?.icon ?? firstHit.company_icon ?? null,
          },
          activeMatches: compDoc?.active_posting_count ?? group.found ?? group.hits.length,
          yearMatches: compDoc?.year_posting_count ?? 0,
          postings: mapHitsToPostingsByFreshness(group.hits),
        };
      })
      .filter((c) => c.postings.length > 0);

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
      const activeFilter = `company_id:=${companyId} && ${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`;
      const q = keywords.length ? keywords.join(" ") : "*";
      const client = getSearchClient();

      const result: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
        () =>
          client
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
            }),
        { label: "loadPostings" },
      );

      return (result.hits ?? []).map((hit: JobPostingHit) =>
        mapHitToPosting(hit, locationIds),
      );
    } catch (err) {
      console.error("[typesense] search error", err);
      return [];
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
        withTypesenseRetry(
          () =>
            client
              .collections<JobPostingDoc>("job_posting")
              .documents()
              .search({
                q,
                query_by: "title",
                filter_by: `${POSTING_BASE_FILTER} && ${baseFilter}`,
                sort_by: keywords.length
                  ? "_text_match:desc,first_seen_at:desc"
                  : "first_seen_at:desc",
                per_page: limit,
                page: Math.floor(offset / limit) + 1,
              }),
          { label: "loadPostingsWithCounts.postings" },
        ),
        withTypesenseRetry(
          () =>
            client
              .collections<JobPostingDoc>("job_posting")
              .documents()
              .search({
                q,
                query_by: "title",
                filter_by: `${POSTING_BASE_FILTER} && ${baseFilter}`,
                per_page: 0,
              }),
          { label: "loadPostingsWithCounts.activeCount" },
        ),
        withTypesenseRetry(
          () =>
            client
              .collections<JobPostingDoc>("job_posting")
              .documents()
              .search({
                q,
                query_by: "title",
                filter_by: `${POSTING_FLOW_FILTER} && first_seen_at:>${oneYearAgoUnix()} && ${baseFilter}`,
                per_page: 0,
              }),
          { label: "loadPostingsWithCounts.yearCount" },
        ),
      ]);

      const postings = (postingsResult.hits ?? []).map(
        (hit: JobPostingHit) => mapHitToPosting(hit, locationIds),
      );
      const activeCount = activeResult.found;
      const yearCount = yearResult.found;

      return { postings, activeCount, yearCount };
    } catch (err) {
      console.error("[typesense] search error", err);
      return { postings: [], activeCount: 0, yearCount: 0 };
    }
  }

  async getSalaryHistogram(
    filters?: HistogramFilters,
  ): Promise<SalaryBucket[]> {
    try {
      const f = filters ?? {};
      const filterStr = buildFilterString(f);
      const hasKeywords = f.keywords && f.keywords.length > 0;
      const q = hasKeywords ? f.keywords!.join(" ") : "*";
      const client = getSearchClient();

      const filterBy = `${POSTING_BASE_FILTER} && salary_eur:>0${filterStr ? " && " + filterStr : ""}`;

      const result: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
        () =>
          client
            .collections<JobPostingDoc>("job_posting")
            .documents()
            .search({
              q,
              query_by: "title",
              filter_by: filterBy,
              facet_by: SALARY_FACET_BY,
              max_facet_values: 31,
              per_page: 0,
            }),
        { label: "salaryHistogram" },
      );

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
      console.error("[typesense] search error", err);
      return [];
    }
  }

  async getExperienceHistogram(
    filters?: HistogramFilters,
  ): Promise<ExperienceBucket[]> {
    try {
      const f = filters ?? {};
      const filterStr = buildFilterString(f);
      const hasKeywords = f.keywords && f.keywords.length > 0;
      const q = hasKeywords ? f.keywords!.join(" ") : "*";
      const client = getSearchClient();

      const filterBy = `${POSTING_BASE_FILTER} && experience_min:>=0${filterStr ? " && " + filterStr : ""}`;

      const result: TsSearchResponse<JobPostingDoc> = await withTypesenseRetry(
        () =>
          client
            .collections<JobPostingDoc>("job_posting")
            .documents()
            .search({
              q,
              query_by: "title",
              filter_by: filterBy,
              facet_by: "experience_min",
              max_facet_values: 30,
              per_page: 0,
            }),
        { label: "experienceHistogram" },
      );

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
      console.error("[typesense] search error", err);
      return [];
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
