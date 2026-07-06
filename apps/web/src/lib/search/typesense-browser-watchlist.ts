import { getTypesenseBrowserConfig, type TypesenseBrowserConfig } from "./typesense-browser-key";
import { buildFilterString, POSTING_BASE_FILTER } from "./typesense-filters";
import { COMPANY_BATCH_SIZE } from "./constants";
import { isTypesenseQueryStringSafe } from "./typesense-query-size";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";

interface JobPostingDoc {
  id: string;
  title?: string;
  source_url?: string;
  first_seen_at?: number;
  is_active?: boolean;
  company_id?: string;
  company_name?: string;
  company_slug?: string;
  company_icon?: string;
}

interface SearchHit<T> {
  document: T;
}

interface RawSearchResponse<T> {
  found?: number;
  hits?: SearchHit<T>[];
}

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
  if (!res.ok) throw new Error(`typesense ${collection} ${res.status}`);
  return res.json();
}

function mapHit(doc: JobPostingDoc): WatchlistPostingEntry {
  return {
    id: doc.id,
    title: doc.title ?? null,
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
 * For larger requests, throws so the runner falls back to the server action
 * (which has a batched/merge implementation we don't need to duplicate
 * browser-side).
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
  const result = await searchOne<JobPostingDoc>(cfg, "job_posting", searchParams);

  const total = result.found ?? 0;
  if (total === 0 || params.limit === 0) return { postings: [], total };
  return {
    postings: (result.hits ?? []).map((h) => mapHit(h.document)),
    total,
  };
}
