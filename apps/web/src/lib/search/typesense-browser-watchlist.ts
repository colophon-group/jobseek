import {
  getTypesenseBrowserConfig,
  invalidateTypesenseBrowserConfigIfUnauthorized,
  type TypesenseBrowserConfig,
} from "./typesense-browser-key";
import {
  buildFilterString,
  POSTING_BASE_FILTER,
  POSTING_FLOW_FILTER,
} from "./typesense-filters";
import { COMPANY_BATCH_SIZE } from "./constants";
import { isTypesenseQueryStringSafe } from "./typesense-query-size";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";
import { normalizePostingTitle } from "@/lib/posting-title";

interface JobPostingDoc {
  id: string;
  title?: string | null;
  source_url?: string | null;
  first_seen_at: number;
  is_active?: boolean | null;
  company_id?: string | null;
  company_name?: string | null;
  company_slug?: string | null;
  company_icon?: string | null;
  location_names?: string[];
}

interface SearchHit<T> {
  document: T;
}

interface RawSearchResponse {
  found: number;
  hits?: SearchHit<Record<string, unknown>>[];
}

async function searchOne(
  cfg: TypesenseBrowserConfig,
  collection: string,
  params: Record<string, unknown>,
): Promise<unknown> {
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
    invalidateTypesenseBrowserConfigIfUnauthorized(res.status);
    throw new Error(`typesense ${collection} ${res.status}`);
  }
  return res.json();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertSearchResponse(
  value: unknown,
  options: { expectHits?: boolean } = {},
): asserts value is RawSearchResponse {
  if (!isRecord(value)) throw new Error("Typesense response was malformed");
  const found = value.found;
  if (typeof found !== "number" || !Number.isInteger(found) || found < 0) {
    throw new Error("Typesense response was malformed");
  }
  const hits = value.hits;
  if (hits !== undefined && !Array.isArray(hits)) {
    throw new Error("Typesense response was malformed");
  }
  if (options.expectHits && found > 0 && !Array.isArray(hits)) {
    throw new Error("Typesense response was malformed");
  }
  if (
    Array.isArray(hits) &&
    hits.some((hit) => !isRecord(hit) || !isRecord(hit.document))
  ) {
    throw new Error("Typesense response was malformed");
  }
}

function assertSearchPageCardinality(
  result: RawSearchResponse,
  params: { offset: number; limit: number },
): void {
  const hits = result.hits ?? [];
  const expectedHits =
    params.limit === 0
      ? 0
      : Math.min(params.limit, Math.max(0, result.found - params.offset));
  if (hits.length !== expectedHits) {
    throw new Error("Typesense response was malformed");
  }
}

function assertJobPostingDoc(
  value: Record<string, unknown>,
): asserts value is Record<string, unknown> & JobPostingDoc {
  const optionalString = (candidate: unknown) =>
    candidate == null || typeof candidate === "string";
  if (
    typeof value.id !== "string" ||
    !optionalString(value.title) ||
    !optionalString(value.source_url) ||
    typeof value.first_seen_at !== "number" ||
    !Number.isFinite(value.first_seen_at) ||
    (value.is_active != null && typeof value.is_active !== "boolean") ||
    !optionalString(value.company_id) ||
    !optionalString(value.company_name) ||
    !optionalString(value.company_slug) ||
    !optionalString(value.company_icon)
  ) {
    throw new Error("Typesense response was malformed");
  }

  const firstSeenAt = new Date(value.first_seen_at * 1000);
  if (!Number.isFinite(firstSeenAt.getTime())) {
    throw new Error("Typesense response was malformed");
  }
}

function mapHit(doc: Record<string, unknown>): WatchlistPostingEntry {
  assertJobPostingDoc(doc);
  return {
    id: doc.id,
    title: normalizePostingTitle(doc.title),
    locationNames: Array.isArray(doc.location_names)
      ? doc.location_names.filter(
          (name): name is string =>
            typeof name === "string" && name.length > 0,
        )
      : [],
    sourceUrl: doc.source_url ?? "",
    firstSeenAt: new Date((doc.first_seen_at ?? 0) * 1000).toISOString(),
    isActive: doc.is_active ?? true,
    company: {
      id: doc.company_id ?? "",
      name: doc.company_name ?? "",
      slug: doc.company_slug ?? "",
      icon: doc.company_icon ?? null,
    },
  };
}

export interface WatchlistPostingsParams {
  companyIds: string[];
  anyCompany?: boolean;
  offset: number;
  limit: number;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  /** Work-mode filter — `onsite | hybrid | remote` (issue #3037). */
  workMode?: ("onsite" | "hybrid" | "remote")[];
  /** Employment-type filter (issue #3037). */
  employmentType?: string[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
}

/**
 * Browser-side watchlist postings fetch. Mirrors the server-side single-query
 * path when the request fits Typesense's GET query-string limit.
 *
 * For larger requests, throws. Interactive pagination may use its existing
 * server fallback; anonymous shell refreshes instead keep their rendered SSR
 * snapshot so a degraded mount never consumes Fluid CPU.
 */
export async function getWatchlistPostingsBrowser(
  params: WatchlistPostingsParams,
): Promise<{ postings: WatchlistPostingEntry[]; total: number }> {
  if (!params.anyCompany && params.companyIds.length === 0) {
    return { postings: [], total: 0 };
  }
  if (params.companyIds.length > COMPANY_BATCH_SIZE) {
    throw new Error("watchlist exceeds COMPANY_BATCH_SIZE — falling back");
  }

  const filterStr = buildFilterString({
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    workMode: params.workMode?.length ? params.workMode : undefined,
    employmentTypes: params.employmentType?.length ? params.employmentType : undefined,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
    languages: params.languages,
  });
  const hasKeywords = params.keywords && params.keywords.length > 0;
  const q = hasKeywords ? params.keywords!.join(" ") : "*";

  const filterParts = [POSTING_BASE_FILTER];
  if (params.companyIds.length > 0) {
    filterParts.push(`company_id:[${params.companyIds.join(",")}]`);
  }
  if (filterStr) filterParts.push(filterStr);

  const searchParams = {
    q,
    query_by: "title",
    filter_by: filterParts.join(" && "),
    sort_by: hasKeywords ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
    per_page: params.limit === 0 ? 0 : params.limit,
    page: params.limit === 0 ? 1 : Math.floor(params.offset / params.limit) + 1,
  };
  if (!isTypesenseQueryStringSafe(searchParams)) {
    throw new Error("watchlist Typesense query exceeds GET limit — falling back");
  }

  const cfg = await getTypesenseBrowserConfig();
  const result = await searchOne(cfg, "job_posting", searchParams);
  assertSearchResponse(result, { expectHits: params.limit !== 0 });
  assertSearchPageCardinality(result, params);

  const total = result.found;
  if (total === 0 || params.limit === 0) return { postings: [], total };
  return {
    postings: (result.hits ?? []).map((hit) => mapHit(hit.document)),
    total,
  };
}

/** Browser-side counterpart to the server's flow count (active state excluded). */
export async function getWatchlistPostingYearCountBrowser(
  params: Omit<WatchlistPostingsParams, "offset" | "limit">,
): Promise<number> {
  if (!params.anyCompany && params.companyIds.length === 0) return 0;
  if (params.companyIds.length > COMPANY_BATCH_SIZE) {
    throw new Error("watchlist exceeds COMPANY_BATCH_SIZE");
  }

  const filterStr = buildFilterString({
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    workMode: params.workMode?.length ? params.workMode : undefined,
    employmentTypes: params.employmentType?.length
      ? params.employmentType
      : undefined,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
    languages: params.languages,
  });
  const q = params.keywords?.length ? params.keywords.join(" ") : "*";
  const oneYearAgo = Math.floor(
    (Date.now() - 365 * 24 * 60 * 60 * 1000) / 1000,
  );
  const filterParts = [POSTING_FLOW_FILTER, `first_seen_at:>${oneYearAgo}`];
  if (params.companyIds.length > 0) {
    filterParts.push(`company_id:[${params.companyIds.join(",")}]`);
  }
  if (filterStr) filterParts.push(filterStr);
  const searchParams = {
    q,
    query_by: "title",
    filter_by: filterParts.join(" && "),
    per_page: 0,
  };
  if (!isTypesenseQueryStringSafe(searchParams)) {
    throw new Error("watchlist Typesense year-count query exceeds GET limit");
  }

  const cfg = await getTypesenseBrowserConfig();
  const result = await searchOne(cfg, "job_posting", searchParams);
  assertSearchResponse(result);
  return result.found;
}
