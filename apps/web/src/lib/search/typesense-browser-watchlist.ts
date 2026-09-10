import {
  getTypesenseBrowserConfig,
  invalidateTypesenseBrowserConfigIfUnauthorized,
  type TypesenseBrowserConfig,
} from "./typesense-browser-key";
import { buildFilterString, POSTING_FLOW_FILTER } from "./typesense-filters";
import { COMPANY_BATCH_SIZE } from "./constants";
import {
  isTypesenseQueryStringSafe,
  splitValuesForTypesenseQuery,
} from "./typesense-query-size";
import type {
  WatchlistCandidateFilters,
  WatchlistPostingEntry,
} from "@/lib/watchlist-matcher-contract";
import { normalizePostingTitle } from "@/lib/posting-title";
import {
  buildWatchlistCandidateSearchParams,
  hasWatchlistCandidateScope,
} from "./watchlist-candidate-query";

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
  text_match?: number;
}

interface RawSearchResponse {
  found: number;
  hits?: SearchHit<Record<string, unknown>>[];
}

const TYPESENSE_MAX_PAGE_SIZE = 250;
const TYPESENSE_BATCH_SAFETY_OFFSET = Number.MAX_SAFE_INTEGER;

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

async function mapWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  mapper: (value: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(values.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(concurrency, values.length) }, async () => {
      while (next < values.length) {
        const index = next++;
        results[index] = await mapper(values[index]);
      }
    }),
  );
  return results;
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

export interface WatchlistPostingsParams extends WatchlistCandidateFilters {
  offset: number;
  limit: number;
}

/**
 * Browser-side watchlist postings fetch. Large persisted watchlists are split
 * into URL-safe batches and merged with the same global ordering as the
 * canonical matcher query. Browser requests are concurrency-bounded so one
 * valid large list cannot create an unbounded burst against Typesense.
 */
export async function getWatchlistPostingsBrowser(
  params: WatchlistPostingsParams,
): Promise<{ postings: WatchlistPostingEntry[]; total: number }> {
  if (!hasWatchlistCandidateScope(params)) {
    return { postings: [], total: 0 };
  }
  const companyIds = params.anyCompany ? [] : [...new Set(params.companyIds)];

  const searchParams = buildWatchlistCandidateSearchParams({
    filters: { ...params, companyIds },
    offset: params.offset,
    limit: params.limit,
  });
  const buildBatchPageSearchParams = (
    batch: readonly string[],
    offset: number,
    limit: number,
  ) => buildWatchlistCandidateSearchParams({
    filters: {
      ...params,
      companyIds: batch,
      anyCompany: batch.length === 0 ? params.anyCompany : false,
    },
    offset,
    limit,
  });
  const buildBatchWindowSearchParams = (
    batch: readonly string[],
    offset: number,
    limit: number,
  ) => {
    const {
      page: _page,
      per_page: _perPage,
      ...candidateSearchParams
    } = buildBatchPageSearchParams(batch, offset, limit);
    return { ...candidateSearchParams, offset, limit };
  };
  const buildBatchSafetyParams = (batch: readonly string[]) =>
    buildBatchWindowSearchParams(
      batch,
      TYPESENSE_BATCH_SAFETY_OFFSET,
      TYPESENSE_MAX_PAGE_SIZE,
    );

  const useSingleQuery =
    companyIds.length <= COMPANY_BATCH_SIZE
    && isTypesenseQueryStringSafe(buildBatchSafetyParams(companyIds));
  if (useSingleQuery) {
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

  const needed = params.offset + params.limit;
  const batches = companyIds.length === 0
    ? [[]]
    : splitValuesForTypesenseQuery(
        companyIds,
        buildBatchSafetyParams,
        COMPANY_BATCH_SIZE,
      );
  if (batches.some((batch) => !isTypesenseQueryStringSafe(
    buildBatchSafetyParams(batch),
  ))) {
    throw new Error("watchlist Typesense query exceeds GET limit — falling back");
  }

  const cfg = await getTypesenseBrowserConfig();
  const resultsByBatch = await mapWithConcurrency(batches, 4, async (batch) => {
    const pages: RawSearchResponse[] = [];

    if (params.limit === 0) {
      const result = await searchOne(
        cfg,
        "job_posting",
        buildBatchPageSearchParams(batch, 0, 0),
      );
      assertSearchResponse(result);
      assertSearchPageCardinality(result, { offset: 0, limit: 0 });
      pages.push(result);
      return pages;
    }

    // An exact global page can contain hits from any company batch, so each
    // disjoint batch must contribute its top `offset + limit` candidates.
    // Walk that prefix in <=250-row windows, including an exact-size final
    // window, rather than downloading a full 250 rows for every load-more.
    for (let batchOffset = 0; batchOffset < needed;) {
      const requestLimit = Math.min(
        TYPESENSE_MAX_PAGE_SIZE,
        needed - batchOffset,
      );
      const result = await searchOne(
        cfg,
        "job_posting",
        buildBatchWindowSearchParams(batch, batchOffset, requestLimit),
      );
      assertSearchResponse(result, { expectHits: true });
      assertSearchPageCardinality(result, {
        offset: batchOffset,
        limit: requestLimit,
      });
      pages.push(result);
      batchOffset += requestLimit;
      if (batchOffset >= result.found) break;
    }
    return pages;
  });

  const total = resultsByBatch.reduce(
    (sum, pages) => sum + (pages[0]?.found ?? 0),
    0,
  );
  if (total === 0 || params.limit === 0) return { postings: [], total };

  const allHits = resultsByBatch.flatMap((pages, batchIndex) => {
    let hitRank = 0;
    return pages.flatMap((result) =>
      (result.hits ?? []).map((hit) => ({
        hit,
        batchIndex,
        hitRank: hitRank++,
      })),
    );
  });
  const sortsByTextMatch = searchParams.sort_by.startsWith("_text_match:");
  allHits.sort((a, b) => {
    if (sortsByTextMatch) {
      const relevance = (b.hit.text_match ?? 0) - (a.hit.text_match ?? 0);
      if (relevance !== 0) return relevance;
    }
    const freshness = Number(b.hit.document.first_seen_at ?? 0)
      - Number(a.hit.document.first_seen_at ?? 0);
    if (freshness !== 0) return freshness;
    // Typesense uses insertion order after the explicit sort keys tie. Keep
    // that per-batch rank intact and define batch order as the cross-batch
    // tie-break so expanding the fetched prefix cannot reorder earlier pages.
    return a.batchIndex - b.batchIndex || a.hitRank - b.hitRank;
  });
  return {
    postings: allHits
      .slice(params.offset, params.offset + params.limit)
      .map(({ hit }) => mapHit(hit.document)),
    total,
  };
}

/** Browser-side counterpart to the server's flow count (active state excluded). */
export async function getWatchlistPostingYearCountBrowser(
  params: Omit<WatchlistPostingsParams, "offset" | "limit">,
): Promise<number> {
  if (!hasWatchlistCandidateScope(params)) return 0;
  const companyIds = params.anyCompany ? [] : [...new Set(params.companyIds)];

  // Reuse the canonical compiler's identifier validation before constructing
  // the flow-count variant, whose base filter intentionally includes inactive
  // postings and therefore cannot use the candidate query verbatim.
  buildWatchlistCandidateSearchParams({
    filters: { ...params, companyIds },
    offset: 0,
    limit: 0,
  });

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
  const buildSearchParams = (batch: readonly string[]) => ({
    q,
    query_by: "title",
    filter_by: [
      POSTING_FLOW_FILTER,
      `first_seen_at:>${oneYearAgo}`,
      ...(batch.length > 0 ? [`company_id:[${batch.join(",")}]`] : []),
      ...(filterStr ? [filterStr] : []),
    ].join(" && "),
    per_page: 0,
  });
  const batches = companyIds.length === 0
    ? [[]]
    : splitValuesForTypesenseQuery(
        companyIds,
        buildSearchParams,
        COMPANY_BATCH_SIZE,
      );
  if (batches.some((batch) => !isTypesenseQueryStringSafe(
    buildSearchParams(batch),
  ))) {
    throw new Error("watchlist Typesense year-count query exceeds GET limit");
  }

  const cfg = await getTypesenseBrowserConfig();
  const results = await mapWithConcurrency(batches, 4, async (batch) => {
    const result = await searchOne(cfg, "job_posting", buildSearchParams(batch));
    assertSearchResponse(result);
    return result;
  });
  return results.reduce((sum, result) => sum + result.found, 0);
}
