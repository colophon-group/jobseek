import { buildFilterString, POSTING_BASE_FILTER, POSTING_FLOW_FILTER } from "./typesense-filters";
import {
  getTypesenseBrowserConfig,
  type TypesenseBrowserConfig,
} from "./typesense-browser-key";
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
  technology_ids: number[];
  employment_type?: string;
  salary_eur?: number;
  experience_min: number;
  locales: string[];
  first_seen_at: number;
}

interface CompanyDoc {
  id: string;
  name: string;
  slug: string;
  icon?: string;
  active_posting_count: number;
  year_posting_count: number;
}

type SearchHit<T> = { document: T; text_match?: number };
type GroupedHit<T> = { group_key: string[]; hits: SearchHit<T>[]; found?: number };
type FacetCount = {
  field_name: string;
  counts: { value: string; count: number }[];
  stats?: { total_values?: number };
};

interface RawSearchResponse<T> {
  found: number;
  hits?: SearchHit<T>[];
  grouped_hits?: GroupedHit<T>[];
  facet_counts?: FacetCount[];
  search_time_ms?: number;
}

function oneYearAgoUnix(): number {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return Math.floor(d.getTime() / 1000);
}

const SALARY_FACET_RANGES = [
  ...Array.from({ length: 30 }, (_, i) => {
    const min = i * 10000;
    const max = (i + 1) * 10000;
    return `${min / 1000}-${max / 1000}k:[${min},${max}]`;
  }),
  "300k+:[300000,999999999]",
].join(", ");

const SALARY_FACET_BY = `salary_eur(${SALARY_FACET_RANGES})`;

async function searchOne<T>(
  cfg: TypesenseBrowserConfig,
  collection: string,
  params: Record<string, unknown>,
): Promise<RawSearchResponse<T>> {
  const url = `${cfg.protocol}://${cfg.host}:${cfg.port}/collections/${collection}/documents/search`;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    qs.set(k, String(v));
  }
  const res = await fetch(`${url}?${qs.toString()}`, {
    method: "GET",
    headers: { "x-typesense-api-key": cfg.apiKey },
  });
  if (!res.ok) {
    throw new Error(`typesense ${collection} search ${res.status}`);
  }
  return res.json();
}

function buildLocations(
  doc: JobPostingDoc,
  filteredLocationIds?: number[],
): PostingLocation[] {
  const locations = (doc.location_names ?? []).map((name, i) => ({
    name,
    type: doc.location_types?.[i] ?? "onsite",
    geoType: (doc.location_geo_types?.[i] ?? undefined) as
      | "city"
      | "region"
      | "country"
      | "macro"
      | undefined,
    _locationId: doc.location_ids?.[i],
  }));
  if (filteredLocationIds?.length) {
    const set = new Set(filteredLocationIds);
    locations.sort((a, b) => (set.has(a._locationId) ? 0 : 1) - (set.has(b._locationId) ? 0 : 1));
  }
  return locations.map(({ _locationId: _, ...rest }) => rest);
}

function mapHitToPosting(
  hit: SearchHit<JobPostingDoc>,
  filteredLocationIds?: number[],
): SearchResultPosting {
  return {
    id: hit.document.id,
    title: hit.document.title || null,
    firstSeenAt: new Date(hit.document.first_seen_at * 1000),
    relevanceScore: hit.text_match ?? 0,
    locations: buildLocations(hit.document, filteredLocationIds),
    isActive: hit.document.is_active,
  };
}

function emptyResponse(): SearchResponse {
  return { companies: [], totalCompanies: 0, degraded: true };
}

async function fetchYearCounts(
  cfg: TypesenseBrowserConfig,
  companyIds: string[],
  filterStr: string,
  q: string,
): Promise<Map<string, number>> {
  if (companyIds.length === 0) return new Map();
  const yearFilter =
    `${POSTING_FLOW_FILTER} && first_seen_at:>${oneYearAgoUnix()} && company_id:[${companyIds.join(",")}]` +
    (filterStr ? ` && ${filterStr}` : "");
  const r = await searchOne<JobPostingDoc>(cfg, "job_posting", {
    q,
    query_by: "title",
    filter_by: yearFilter,
    facet_by: "company_id",
    facet_strategy: "exhaustive",
    max_facet_values: companyIds.length,
    per_page: 0,
  });
  const counts = r.facet_counts?.[0]?.counts ?? [];
  return new Map(counts.map((c) => [c.value, c.count]));
}

export class TypesenseBrowserProvider implements SearchProvider {
  private async cfg(): Promise<TypesenseBrowserConfig> {
    return getTypesenseBrowserConfig();
  }

  async search(
    params: SearchFilters & { keywords: string[]; offset: number; limit: number },
  ): Promise<SearchResponse> {
    try {
      const cfg = await this.cfg();
      const { keywords, offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      const activeFilter = `${POSTING_BASE_FILTER}${filterStr ? ` && ${filterStr}` : ""}`;

      const result = await searchOne<JobPostingDoc>(cfg, "job_posting", {
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

      const totalCompanies = result.facet_counts?.[0]?.stats?.total_values ?? 0;
      const groupedHits = (result.grouped_hits ?? []) as GroupedHit<JobPostingDoc>[];
      const companyIds = groupedHits.map((g) => g.hits[0].document.company_id);
      const yearMap = await fetchYearCounts(cfg, companyIds, filterStr, keywords.join(" "));

      const companies: SearchResultCompany[] = groupedHits.map((g) => {
        const first = g.hits[0].document;
        const cid = first.company_id;
        return {
          company: { id: cid, name: first.company_name, slug: first.company_slug, icon: first.company_icon ?? null },
          activeMatches: g.found ?? 0,
          yearMatches: yearMap.get(cid) ?? 0,
          postings: g.hits.map((h) => mapHitToPosting(h, locationIds)),
        };
      });

      return { companies, totalCompanies };
    } catch (err) {
      console.error("[typesense-browser] search error", err);
      return emptyResponse();
    }
  }

  async listTopCompanies(
    params: SearchFilters & { offset: number; limit: number },
  ): Promise<SearchResponse> {
    try {
      const cfg = await this.cfg();
      const { offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      if (filterStr.length === 0) {
        return await this.unfiltered(cfg, offset, limit);
      }
      return await this.filtered(cfg, filterStr, offset, limit, locationIds);
    } catch (err) {
      console.error("[typesense-browser] listTopCompanies error", err);
      return emptyResponse();
    }
  }

  private async filtered(
    cfg: TypesenseBrowserConfig,
    filterStr: string,
    offset: number,
    limit: number,
    locationIds?: number[],
  ): Promise<SearchResponse> {
    const activeFilter = `${POSTING_BASE_FILTER} && ${filterStr}`;
    const facetResult = await searchOne<JobPostingDoc>(cfg, "job_posting", {
      q: "*",
      filter_by: activeFilter,
      facet_by: "company_id",
      facet_strategy: "exhaustive",
      max_facet_values: offset + limit,
      per_page: 0,
    });

    const facets = facetResult.facet_counts?.[0];
    const totalCompanies = facets?.stats?.total_values ?? 0;
    const all = facets?.counts ?? [];
    const page = all.slice(offset, offset + limit);
    if (page.length === 0) return { companies: [], totalCompanies };

    const companyIds = page.map((c) => c.value);
    const activeMap = new Map(page.map((c) => [c.value, c.count] as [string, number]));

    const [yearMap, postingResults] = await Promise.all([
      fetchYearCounts(cfg, companyIds, filterStr, "*"),
      searchOne<JobPostingDoc>(cfg, "job_posting", {
        q: "*",
        filter_by: `company_id:[${companyIds.join(",")}] && ${activeFilter}`,
        group_by: "company_id",
        group_limit: 10,
        sort_by: "first_seen_at:desc",
        per_page: companyIds.length,
      }),
    ]);

    const groupMap = new Map<string, GroupedHit<JobPostingDoc>>(
      (postingResults.grouped_hits ?? []).map(
        (g) => [g.hits[0].document.company_id, g] as [string, GroupedHit<JobPostingDoc>],
      ),
    );

    const companies: SearchResultCompany[] = companyIds
      .map((cid) => {
        const g = groupMap.get(cid);
        if (!g) return null;
        const first = g.hits[0].document;
        return {
          company: { id: cid, name: first.company_name, slug: first.company_slug, icon: first.company_icon ?? null },
          activeMatches: activeMap.get(cid) ?? 0,
          yearMatches: yearMap.get(cid) ?? 0,
          postings: g.hits.map((h) => mapHitToPosting(h, locationIds)),
        } satisfies SearchResultCompany;
      })
      .filter((c): c is SearchResultCompany => c !== null);

    return { companies, totalCompanies };
  }

  private async unfiltered(
    cfg: TypesenseBrowserConfig,
    offset: number,
    limit: number,
  ): Promise<SearchResponse> {
    const companyResults = await searchOne<CompanyDoc>(cfg, "company", {
      q: "*",
      filter_by: "active_posting_count:>0",
      sort_by: "active_posting_count:desc",
      per_page: limit,
      page: Math.floor(offset / limit) + 1,
    });
    const totalCompanies = companyResults.found;
    const hits = companyResults.hits ?? [];
    if (hits.length === 0) return { companies: [], totalCompanies };

    const companyIds = hits.map((h) => h.document.id);
    const compMap = new Map<string, CompanyDoc>(hits.map((h) => [h.document.id, h.document]));
    const postingResults = await searchOne<JobPostingDoc>(cfg, "job_posting", {
      q: "*",
      filter_by: `company_id:[${companyIds.join(",")}] && ${POSTING_BASE_FILTER}`,
      group_by: "company_id",
      group_limit: 10,
      sort_by: "first_seen_at:desc",
      per_page: companyIds.length,
    });

    const groupMap = new Map<string, GroupedHit<JobPostingDoc>>(
      (postingResults.grouped_hits ?? []).map(
        (g) => [g.hits[0].document.company_id, g] as [string, GroupedHit<JobPostingDoc>],
      ),
    );

    const companies: SearchResultCompany[] = companyIds
      .map((cid) => {
        const compDoc = compMap.get(cid);
        const g = groupMap.get(cid);
        if (!compDoc) return null;
        return {
          company: { id: cid, name: compDoc.name, slug: compDoc.slug, icon: compDoc.icon ?? null },
          activeMatches: compDoc.active_posting_count,
          yearMatches: compDoc.year_posting_count,
          postings: g ? g.hits.map((h) => mapHitToPosting(h)) : [],
        } satisfies SearchResultCompany;
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
      const cfg = await this.cfg();
      const { companyId, keywords, offset, limit, locationIds } = params;
      const filterStr = buildFilterString(params);
      const activeFilter =
        `company_id:=${companyId} && ${POSTING_BASE_FILTER}` + (filterStr ? ` && ${filterStr}` : "");
      const q = keywords.length ? keywords.join(" ") : "*";
      const r = await searchOne<JobPostingDoc>(cfg, "job_posting", {
        q,
        query_by: "title",
        filter_by: activeFilter,
        sort_by: keywords.length
          ? "_text_match:desc,first_seen_at:desc"
          : "first_seen_at:desc",
        per_page: limit,
        page: Math.floor(offset / limit) + 1,
      });
      return (r.hits ?? []).map((h) => mapHitToPosting(h, locationIds));
    } catch (err) {
      console.error("[typesense-browser] loadPostings error", err);
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
    // Throws on transport / Typesense error so the runner can distinguish
    // "errored, fall back to server action" from "legitimate zero matches".
    const cfg = await this.cfg();
    const { companyId, keywords, offset, limit, locationIds } = params;
    const filterStr = buildFilterString(params);
    const baseFilter = `company_id:=${companyId}${filterStr ? ` && ${filterStr}` : ""}`;
    const q = keywords.length ? keywords.join(" ") : "*";

    const [postingsResult, activeResult, yearResult] = await Promise.all([
      searchOne<JobPostingDoc>(cfg, "job_posting", {
        q,
        query_by: "title",
        filter_by: `${POSTING_BASE_FILTER} && ${baseFilter}`,
        sort_by: keywords.length
          ? "_text_match:desc,first_seen_at:desc"
          : "first_seen_at:desc",
        per_page: limit,
        page: Math.floor(offset / limit) + 1,
      }),
      searchOne<JobPostingDoc>(cfg, "job_posting", {
        q,
        query_by: "title",
        filter_by: `${POSTING_BASE_FILTER} && ${baseFilter}`,
        per_page: 0,
      }),
      searchOne<JobPostingDoc>(cfg, "job_posting", {
        q,
        query_by: "title",
        filter_by: `${POSTING_FLOW_FILTER} && first_seen_at:>${oneYearAgoUnix()} && ${baseFilter}`,
        per_page: 0,
      }),
    ]);

    const postings = (postingsResult.hits ?? []).map((h) =>
      mapHitToPosting(h, locationIds),
    );
    return {
      postings,
      activeCount: activeResult.found ?? 0,
      yearCount: yearResult.found ?? 0,
    };
  }

  async getSalaryHistogram(filters?: HistogramFilters): Promise<SalaryBucket[]> {
    try {
      const cfg = await this.cfg();
      const f = filters ?? {};
      const filterStr = buildFilterString(f);
      const hasKeywords = f.keywords && f.keywords.length > 0;
      const q = hasKeywords ? f.keywords!.join(" ") : "*";
      const r = await searchOne<JobPostingDoc>(cfg, "job_posting", {
        q,
        query_by: "title",
        filter_by: `${POSTING_BASE_FILTER} && salary_eur:>0${filterStr ? ` && ${filterStr}` : ""}`,
        facet_by: SALARY_FACET_BY,
        max_facet_values: 31,
        per_page: 0,
      });
      const facet = r.facet_counts?.[0];
      if (!facet) return [];
      const buckets: SalaryBucket[] = [];
      for (const e of facet.counts) {
        const range = parseFacetRange(e.value);
        if (range) buckets.push({ min: range[0], max: range[1], count: e.count });
      }
      buckets.sort((a, b) => a.min - b.min);
      return buckets;
    } catch (err) {
      console.error("[typesense-browser] salary histogram error", err);
      return [];
    }
  }

  async getExperienceHistogram(filters?: HistogramFilters): Promise<ExperienceBucket[]> {
    try {
      const cfg = await this.cfg();
      const f = filters ?? {};
      const filterStr = buildFilterString(f);
      const hasKeywords = f.keywords && f.keywords.length > 0;
      const q = hasKeywords ? f.keywords!.join(" ") : "*";
      const r = await searchOne<JobPostingDoc>(cfg, "job_posting", {
        q,
        query_by: "title",
        filter_by: `${POSTING_BASE_FILTER} && experience_min:>=0${filterStr ? ` && ${filterStr}` : ""}`,
        facet_by: "experience_min",
        max_facet_values: 30,
        per_page: 0,
      });
      const facet = r.facet_counts?.[0];
      if (!facet) return [];
      const buckets: ExperienceBucket[] = [];
      for (const e of facet.counts) {
        const years = parseInt(e.value, 10);
        if (!isNaN(years)) buckets.push({ years, count: e.count });
      }
      buckets.sort((a, b) => a.years - b.years);
      return buckets;
    } catch (err) {
      console.error("[typesense-browser] experience histogram error", err);
      return [];
    }
  }
}

function parseFacetRange(label: string): [number, number] | null {
  if (label.endsWith("+")) {
    const num = parseFloat(label.replace("k+", "")) * 1000;
    if (isNaN(num)) return null;
    return [num, 999999999];
  }
  const m = label.match(/^(\d+)-(\d+)k$/);
  if (m) return [parseInt(m[1], 10) * 1000, parseInt(m[2], 10) * 1000];
  return null;
}

let _provider: TypesenseBrowserProvider | undefined;
export function getBrowserSearchProvider(): TypesenseBrowserProvider {
  if (!_provider) _provider = new TypesenseBrowserProvider();
  return _provider;
}
