import "server-only";

import { getCurrencyRates } from "@/lib/services/search";
import { resolveLocationSlugs, type ResolvedLocation } from "@/lib/services/locations";
import {
  resolveOccupationSlugs,
  resolveSenioritySlugs,
  resolveTechnologySlugs,
  type TaxonomySuggestion,
} from "@/lib/services/taxonomy";
import { resolveJobLanguages } from "@/lib/job-languages";
import { convertToEur } from "@/lib/salary";
import { getSearchClient } from "@/lib/search/typesense-client";
import {
  assertTypesenseSearchResult,
  malformedTypesenseResponseError,
  withTypesenseRetry,
} from "@/lib/search/typesense-retry";
import {
  parseTypesenseMultiSearchResults,
  type TypesenseMultiSearchResult,
} from "@/lib/search/typesense-multi-search";
import {
  isTypesenseQueryStringSafe,
  splitValuesForTypesenseQuery,
  type TypesenseQueryParams,
} from "@/lib/search/typesense-query-size";
import { COMPANY_BATCH_SIZE } from "@/lib/search/constants";
import { normalizePostingTitle } from "@/lib/posting-title";
import { canonicalStringCompare } from "@/lib/sort";
import {
  buildWatchlistCandidateSearchParams,
  candidateOrderKeyFromCanonicalId,
  hasWatchlistCandidateScope,
  WATCHLIST_CANDIDATE_ORDER_KEY_FIELD,
  WATCHLIST_CANDIDATE_WINDOW_BOUNDARY,
  type WatchlistCandidateOrder,
  type WatchlistCandidateSearchParams,
  type WatchlistCandidateWindow,
} from "@/lib/search/watchlist-candidate-query";
import { stableCandidateOrderReady } from "@/lib/search/stable-candidate-order-readiness";
import type {
  CompiledWatchlistMatcher,
  MatchedWatchlistPosting,
  WatchlistCandidateFilters,
  WatchlistMatcherSource,
  WatchlistPostingEntry,
} from "@/lib/watchlist-matcher-contract";

type WorkMode = NonNullable<WatchlistCandidateFilters["workMode"]>[number];

const WORK_MODES = new Set<WorkMode>(["onsite", "hybrid", "remote"]);
const MULTI_SEARCH_CHUNK_SIZE = 40;
const TYPESENSE_MAX_PAGE_SIZE = 250;
const TYPESENSE_BATCH_SAFETY_OFFSET = Number.MAX_SAFE_INTEGER;
const STABLE_CANDIDATE_MISSING_FIRST_SORT =
  `${WATCHLIST_CANDIDATE_ORDER_KEY_FIELD}(missing_values: first):asc`;

export type CompiledWatchlistFilter = CompiledWatchlistMatcher & {
  resolvedLocations: ResolvedLocation[];
  resolvedOccupations: TaxonomySuggestion[];
  resolvedSeniorities: TaxonomySuggestion[];
  resolvedTechnologies: TaxonomySuggestion[];
};

function strings(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const result = value.filter(
    (item): item is string => typeof item === "string" && item.length > 0,
  );
  return result.length > 0 ? result : undefined;
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)];
}

function valuesForSlugs<T>(
  slugs: string[] | undefined,
  resolved: Map<string, T>,
): T[] {
  return (slugs ?? [])
    .map((slug) => resolved.get(slug))
    .filter((value): value is T => value !== undefined);
}

/**
 * Resolve persisted JSONB filters against current taxonomy, currency, and
 * locale state. This function never reads a request session and compiles all
 * supplied watchlists in batches, making it suitable for both a page read and
 * a background user's consolidated digest.
 */
export async function compileWatchlistMatcherSources(
  sources: readonly WatchlistMatcherSource[],
): Promise<CompiledWatchlistFilter[]> {
  if (sources.length === 0) return [];

  const ids = new Set<string>();
  for (const source of sources) {
    if (ids.has(source.watchlistId)) {
      throw new Error(`Duplicate watchlist matcher source: ${source.watchlistId}`);
    }
    ids.add(source.watchlistId);
  }

  const byLocale = new Map<string, WatchlistMatcherSource[]>();
  for (const source of sources) {
    const group = byLocale.get(source.locale) ?? [];
    group.push(source);
    byLocale.set(source.locale, group);
  }

  const localeMaps = new Map<
    string,
    {
      locations: Map<string, ResolvedLocation>;
      occupations: Map<string, TaxonomySuggestion>;
      seniorities: Map<string, TaxonomySuggestion>;
    }
  >();
  await Promise.all(
    [...byLocale.entries()].map(async ([locale, group]) => {
      const locationSlugs = uniqueStrings(
        group.flatMap((source) => strings(source.filters?.locationSlugs) ?? []),
      );
      const occupationSlugs = uniqueStrings(
        group.flatMap((source) => strings(source.filters?.occupationSlugs) ?? []),
      );
      const senioritySlugs = uniqueStrings(
        group.flatMap((source) => strings(source.filters?.senioritySlugs) ?? []),
      );
      const [locations, occupations, seniorities] = await Promise.all([
        locationSlugs.length
          ? resolveLocationSlugs(locationSlugs, locale)
          : Promise.resolve(new Map<string, ResolvedLocation>()),
        occupationSlugs.length
          ? resolveOccupationSlugs(occupationSlugs, locale)
          : Promise.resolve(new Map<string, TaxonomySuggestion>()),
        senioritySlugs.length
          ? resolveSenioritySlugs(senioritySlugs, locale)
          : Promise.resolve(new Map<string, TaxonomySuggestion>()),
      ]);
      localeMaps.set(locale, { locations, occupations, seniorities });
    }),
  );

  const technologySlugs = uniqueStrings(
    sources.flatMap(
      (source) => strings(source.filters?.technologySlugs) ?? [],
    ),
  );
  const needsCurrencyRates = sources.some(
    (source) =>
      finite(source.filters?.salaryMin) !== undefined ||
      finite(source.filters?.salaryMax) !== undefined,
  );
  const [technologies, rates] = await Promise.all([
    technologySlugs.length
      ? resolveTechnologySlugs(technologySlugs)
      : Promise.resolve(new Map<string, TaxonomySuggestion>()),
    needsCurrencyRates ? getCurrencyRates() : Promise.resolve([]),
  ]);

  return sources.map((source) => {
    const raw = source.filters ?? {};
    const maps = localeMaps.get(source.locale)!;
    const locationSlugs = strings(raw.locationSlugs);
    const occupationSlugs = strings(raw.occupationSlugs);
    const senioritySlugs = strings(raw.senioritySlugs);
    const sourceTechnologySlugs = strings(raw.technologySlugs);
    const resolvedLocations = valuesForSlugs(locationSlugs, maps.locations);
    const resolvedOccupations = valuesForSlugs(
      occupationSlugs,
      maps.occupations,
    );
    const resolvedSeniorities = valuesForSlugs(
      senioritySlugs,
      maps.seniorities,
    );
    const resolvedTechnologies = valuesForSlugs(
      sourceTechnologySlugs,
      technologies,
    );
    const rawWorkMode = strings(raw.workMode);
    const workMode = rawWorkMode?.filter((mode): mode is WorkMode =>
      WORK_MODES.has(mode as WorkMode),
    );
    const salaryCurrency =
      typeof raw.salaryCurrency === "string" && raw.salaryCurrency.length > 0
        ? raw.salaryCurrency
        : "EUR";
    const salaryMin = finite(raw.salaryMin);
    const salaryMax = finite(raw.salaryMax);

    return {
      watchlistId: source.watchlistId,
      watchlistLabel: source.watchlistLabel,
      candidateFilters: {
        companyIds:
          raw.anyCompany === true ? [] : uniqueStrings(source.companyIds),
        anyCompany: raw.anyCompany === true ? true : undefined,
        keywords: strings(raw.keywords),
        locationIds: resolvedLocations.map((value) => value.id),
        occupationIds: resolvedOccupations.map((value) => value.id),
        seniorityIds: resolvedSeniorities.map((value) => value.id),
        technologyIds: resolvedTechnologies.map((value) => value.id),
        workMode: workMode?.length ? workMode : undefined,
        employmentType: strings(raw.employmentType),
        salaryMin: convertToEur(salaryMin, salaryCurrency, rates),
        salaryMax: convertToEur(salaryMax, salaryCurrency, rates),
        experienceMin: finite(raw.experienceMin),
        experienceMax: finite(raw.experienceMax),
        languages: resolveJobLanguages(source.jobLanguages, source.locale),
      },
      resolvedLocations,
      resolvedOccupations,
      resolvedSeniorities,
      resolvedTechnologies,
    };
  });
}

type CandidateHit = {
  document: object;
  text_match?: number;
};

type RankedCandidateHit = {
  hit: CandidateHit;
  batchIndex: number;
  hitRank: number;
};

function assertStableCandidateOrderReady(ready: boolean): void {
  if (!ready) {
    throw new Error(
      "Stable Typesense candidate ordering has not passed backfill readiness",
    );
  }
}

function stableCandidateId(hit: CandidateHit): string {
  const doc = hit.document as Record<string, unknown>;
  const id = doc.id;
  const sortKey = doc[WATCHLIST_CANDIDATE_ORDER_KEY_FIELD];
  if (typeof id !== "string" || typeof sortKey !== "string") {
    throw malformedTypesenseResponseError();
  }
  let expected: string;
  try {
    expected = candidateOrderKeyFromCanonicalId(id);
  } catch {
    throw malformedTypesenseResponseError();
  }
  if (expected !== sortKey) throw malformedTypesenseResponseError();
  return id;
}

function stableCandidateGuardParams<T extends WatchlistCandidateSearchParams>(
  params: T,
): T {
  return {
    ...params,
    sort_by: STABLE_CANDIDATE_MISSING_FIRST_SORT,
    per_page: 1,
    page: 1,
  } as T;
}

function assertStableCandidateGuard(
  result: TypesenseMultiSearchResult<object>,
): void {
  assertTypesenseSearchResult(result);
  if (result.found === 0) return;
  const hits = result.hits ?? [];
  if (hits.length !== 1) throw malformedTypesenseResponseError();
  stableCandidateId(hits[0]!);
}

function lexicalCompare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function mapCandidateHit(
  hit: CandidateHit,
  stableNewestReady: boolean,
): WatchlistPostingEntry {
  const doc = hit.document as Record<string, unknown>;
  const optionalString = (value: unknown) =>
    value == null || typeof value === "string";
  if (
    typeof doc.id !== "string" ||
    !optionalString(doc.title) ||
    !optionalString(doc.source_url) ||
    typeof doc.first_seen_at !== "number" ||
    !Number.isFinite(doc.first_seen_at) ||
    (doc.is_active != null && typeof doc.is_active !== "boolean") ||
    !optionalString(doc.company_id) ||
    !optionalString(doc.company_name) ||
    !optionalString(doc.company_slug) ||
    !optionalString(doc.company_icon)
  ) {
    throw malformedTypesenseResponseError();
  }
  if (stableNewestReady) stableCandidateId(hit);

  const firstSeenAt = new Date(doc.first_seen_at * 1_000);
  if (!Number.isFinite(firstSeenAt.getTime())) {
    throw malformedTypesenseResponseError();
  }
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
    firstSeenAt: firstSeenAt.toISOString(),
    isActive: doc.is_active ?? true,
    company: {
      id: doc.company_id ?? "",
      name: doc.company_name ?? "",
      slug: doc.company_slug ?? "",
      icon: doc.company_icon ?? null,
    },
  };
}

function compareRankedHits(
  a: RankedCandidateHit,
  b: RankedCandidateHit,
  order: WatchlistCandidateOrder,
  stableNewestReady: boolean,
): number {
  if (order === "interactive") {
    const relevance = (b.hit.text_match ?? 0) - (a.hit.text_match ?? 0);
    if (relevance !== 0) return relevance;
  }
  const aDoc = a.hit.document as Record<string, unknown>;
  const bDoc = b.hit.document as Record<string, unknown>;
  const freshness =
    ((bDoc.first_seen_at as number) ?? 0) -
    ((aDoc.first_seen_at as number) ?? 0);
  if (freshness !== 0) return freshness;
  if (order === "newest" && stableNewestReady) {
    return lexicalCompare(stableCandidateId(a.hit), stableCandidateId(b.hit));
  }
  // Typesense uses insertion order after the explicit sort keys tie. Preserve
  // that per-batch rank and make batch order the deterministic cross-batch
  // tie-break so a larger requested prefix never reshuffles earlier pages.
  return a.batchIndex - b.batchIndex || a.hitRank - b.hitRank;
}

function assertCandidatePageCardinality(
  result: TypesenseMultiSearchResult<object>,
  params: { offset: number; limit: number },
): void {
  const hits = result.hits ?? [];
  const expectedHits = params.limit === 0
    ? 0
    : Math.min(params.limit, Math.max(0, result.found - params.offset));
  if (hits.length !== expectedHits) {
    throw malformedTypesenseResponseError();
  }
}

function batchesForFilters(
  filters: WatchlistCandidateFilters,
  buildParams: (filters: WatchlistCandidateFilters) => TypesenseQueryParams,
): WatchlistCandidateFilters[] {
  if (filters.anyCompany || filters.companyIds.length === 0) return [filters];
  const batches = splitValuesForTypesenseQuery(
    uniqueStrings(filters.companyIds),
    (companyIds) => buildParams({ ...filters, companyIds }),
    COMPANY_BATCH_SIZE,
  );
  return batches.map((companyIds) => ({ ...filters, companyIds }));
}

/** Session-free canonical reader used beneath the existing interactive action. */
export async function readWatchlistCandidates(params: {
  filters: WatchlistCandidateFilters;
  offset: number;
  limit: number;
  window?: WatchlistCandidateWindow;
  order?: WatchlistCandidateOrder;
  /** AF-2 callers set this so an unverified index cannot degrade silently. */
  requireStableOrder?: boolean;
  abortSignal?: AbortSignal;
}): Promise<{ postings: WatchlistPostingEntry[]; total: number }> {
  const order = params.order ?? "interactive";
  const stableNewestReady =
    order === "newest" &&
    params.requireStableOrder === true &&
    stableCandidateOrderReady();
  if (params.requireStableOrder === true) {
    if (order !== "newest") {
      throw new Error("Stable candidate ordering requires newest-first order");
    }
    assertStableCandidateOrderReady(stableNewestReady);
  }
  if (!hasWatchlistCandidateScope(params.filters)) {
    return { postings: [], total: 0 };
  }
  const buildParams = (filters: WatchlistCandidateFilters) =>
    buildWatchlistCandidateSearchParams({
      filters,
      offset: params.offset,
      limit: params.limit,
      window: params.window,
      order,
      stableNewestReady,
    });
  const searchParams = buildParams(params.filters);
  const buildWindowSearchParams = (
    filters: WatchlistCandidateFilters,
    offset: number,
    limit: number,
  ) => {
    const {
      page: _page,
      per_page: _perPage,
      ...candidateSearchParams
    } = buildWatchlistCandidateSearchParams({
      filters,
      offset,
      limit,
      window: params.window,
      order,
      stableNewestReady,
    });
    return { ...candidateSearchParams, offset, limit };
  };
  const directSearchParams = params.limit === 0
    ? searchParams
    : buildWindowSearchParams(params.filters, params.offset, params.limit);
  const buildBatchSafetyParams = (filters: WatchlistCandidateFilters) =>
    buildWindowSearchParams(
      filters,
      TYPESENSE_BATCH_SAFETY_OFFSET,
      TYPESENSE_MAX_PAGE_SIZE,
    );
  const needsBatches =
    !params.filters.anyCompany &&
    params.filters.companyIds.length > 0 &&
    (params.filters.companyIds.length > COMPANY_BATCH_SIZE ||
      !isTypesenseQueryStringSafe(buildBatchSafetyParams(params.filters)));
  const client = getSearchClient();
  const filterBatches = needsBatches
    ? batchesForFilters(params.filters, (filters) =>
        buildBatchSafetyParams(filters),
      )
    : [params.filters];
  const guardStableCandidateOrder = async () => {
    const guards = await Promise.all(
      filterBatches.map((filters) =>
        withTypesenseRetry(
          () =>
            client.collections("job_posting").documents().search(
              stableCandidateGuardParams(buildWatchlistCandidateSearchParams({
                filters,
                offset: 0,
                limit: 1,
                window: params.window,
                order,
                stableNewestReady,
              })),
              { abortSignal: params.abortSignal },
            ),
          {
            label: "readWatchlistCandidates.stable-order-guard",
            abortSignal: params.abortSignal,
          },
        ),
      ),
    );
    for (const result of guards) assertStableCandidateGuard(result);
  };
  if (stableNewestReady) await guardStableCandidateOrder();
  if (!needsBatches) {
    const result = await withTypesenseRetry(
      () =>
        client
          .collections("job_posting")
          .documents()
          .search(directSearchParams, { abortSignal: params.abortSignal }),
      { label: "readWatchlistCandidates", abortSignal: params.abortSignal },
    );
    assertTypesenseSearchResult(result, { expectHits: params.limit !== 0 });
    if (stableNewestReady) await guardStableCandidateOrder();
    const total = result.found ?? 0;
    return {
      postings:
        total === 0 || params.limit === 0
          ? []
          : (result.hits ?? []).map((hit) =>
              mapCandidateHit(hit, stableNewestReady)
            ),
      total,
    };
  }

  const needed = params.offset + params.limit;
  if (filterBatches.some((filters) => !isTypesenseQueryStringSafe(
    buildBatchSafetyParams(filters),
  ))) {
    throw new Error("watchlist Typesense query exceeds GET limit");
  }
  const countResults = await Promise.all(
    filterBatches.map((filters) =>
      withTypesenseRetry(
        () =>
          client.collections("job_posting").documents().search(
            buildWatchlistCandidateSearchParams({
              filters,
              offset: 0,
              limit: 0,
              window: params.window,
              order,
              stableNewestReady,
            }),
            { abortSignal: params.abortSignal },
          ),
        {
          label: "readWatchlistCandidates.batched.count",
          abortSignal: params.abortSignal,
        },
      ),
    ),
  );
  for (const result of countResults) assertTypesenseSearchResult(result);
  const total = countResults.reduce((sum, result) => sum + (result.found ?? 0), 0);
  if (total === 0 || params.limit === 0) {
    if (stableNewestReady) await guardStableCandidateOrder();
    return { postings: [], total };
  }

  const rowResultsByBatch = await Promise.all(
    filterBatches.map(async (filters) => {
      const pages: TypesenseMultiSearchResult<object>[] = [];
      for (let batchOffset = 0; batchOffset < needed;) {
        const requestLimit = Math.min(
          TYPESENSE_MAX_PAGE_SIZE,
          needed - batchOffset,
        );
        const result = await withTypesenseRetry(
          () =>
            client.collections("job_posting").documents().search(
              buildWindowSearchParams(filters, batchOffset, requestLimit),
              { abortSignal: params.abortSignal },
            ),
          {
            label: "readWatchlistCandidates.batched.rows",
            abortSignal: params.abortSignal,
          },
        );
        assertTypesenseSearchResult(result, { expectHits: true });
        assertCandidatePageCardinality(result, {
          offset: batchOffset,
          limit: requestLimit,
        });
        pages.push(result);
        batchOffset += requestLimit;
        if (batchOffset >= result.found) break;
      }
      return pages;
    }),
  );
  if (stableNewestReady) await guardStableCandidateOrder();
  const allHits = rowResultsByBatch.flatMap((pages, batchIndex) => {
    let hitRank = 0;
    return pages.flatMap((result) =>
      (result.hits ?? []).map((hit) => ({
        hit,
        batchIndex,
        hitRank: hitRank++,
      })),
    );
  });
  allHits.sort((a, b) =>
    compareRankedHits(a, b, order, stableNewestReady)
  );
  return {
    postings: allHits
      .slice(params.offset, params.offset + params.limit)
      .map(({ hit }) => mapCandidateHit(hit, stableNewestReady)),
    total,
  };
}

type SearchPlanEntry = {
  watchlistIndex: number;
  batchIndex: number;
  search: WatchlistCandidateSearchParams & { collection: "job_posting" };
};

export type WatchlistWindowMatchResult = {
  /** Explicit UTC bounds and their shared, documented boundary semantics. */
  window: {
    windowStart: string;
    windowEnd: string;
    boundary: typeof WATCHLIST_CANDIDATE_WINDOW_BOUNDARY;
  };
  postings: MatchedWatchlistPosting[];
  watchlists: Array<{
    id: string;
    label: string;
    total: number;
    returned: number;
    truncated: boolean;
  }>;
};

/**
 * Evaluate many compiled watchlists through bounded Typesense multi_search
 * calls, then deduplicate posting IDs while retaining every matching label.
 */
export async function matchCompiledWatchlistsInWindow(params: {
  watchlists: readonly CompiledWatchlistMatcher[];
  windowStart: Date;
  windowEnd: Date;
  limitPerWatchlist: number;
  /** AF-2 callers set this so an unverified index cannot degrade silently. */
  requireStableOrder?: boolean;
  abortSignal?: AbortSignal;
}): Promise<WatchlistWindowMatchResult> {
  if (
    !Number.isInteger(params.limitPerWatchlist) ||
    params.limitPerWatchlist < 1 ||
    params.limitPerWatchlist > 250
  ) {
    throw new RangeError("limitPerWatchlist must be an integer between 1 and 250");
  }
  const stableNewestReady =
    params.requireStableOrder === true && stableCandidateOrderReady();
  if (params.requireStableOrder === true) {
    assertStableCandidateOrderReady(stableNewestReady);
  }
  const window = {
    windowStart: params.windowStart,
    windowEnd: params.windowEnd,
  } satisfies WatchlistCandidateWindow;
  // Validate even an empty watchlist batch so callers cannot advance an
  // invalid ledger window merely because no filters were due.
  buildWatchlistCandidateSearchParams({
    filters: { companyIds: [], anyCompany: true },
    offset: 0,
    limit: 0,
    window,
    order: "newest",
    stableNewestReady,
  });

  const ids = new Set<string>();
  for (const watchlist of params.watchlists) {
    if (ids.has(watchlist.watchlistId)) {
      throw new Error(`Duplicate compiled watchlist: ${watchlist.watchlistId}`);
    }
    ids.add(watchlist.watchlistId);
  }

  const plan: SearchPlanEntry[] = [];
  params.watchlists.forEach((watchlist, watchlistIndex) => {
    if (!hasWatchlistCandidateScope(watchlist.candidateFilters)) return;
    const batches = batchesForFilters(watchlist.candidateFilters, (filters) =>
      buildWatchlistCandidateSearchParams({
        filters,
        offset: 0,
        limit: params.limitPerWatchlist,
        window,
        order: "newest",
        stableNewestReady,
      }),
    );
    batches.forEach((filters, batchIndex) => {
      plan.push({
        watchlistIndex,
        batchIndex,
        search: {
          collection: "job_posting",
          ...buildWatchlistCandidateSearchParams({
            filters,
            offset: 0,
            limit: params.limitPerWatchlist,
            window,
            order: "newest",
            stableNewestReady,
          }),
        },
      });
    });
  });

  const results: TypesenseMultiSearchResult<object>[] = [];
  if (plan.length > 0) {
    const client = getSearchClient();
    const guardStableCandidateOrder = async () => {
      for (let offset = 0; offset < plan.length; offset += MULTI_SEARCH_CHUNK_SIZE) {
        const chunk = plan.slice(offset, offset + MULTI_SEARCH_CHUNK_SIZE);
        const raw = await withTypesenseRetry(
          () =>
            client.multiSearch.perform(
              {
                searches: chunk.map((entry) =>
                  stableCandidateGuardParams(entry.search)
                ),
              },
              {},
              { abortSignal: params.abortSignal },
            ),
          {
            label: "matchCompiledWatchlistsInWindow.stable-order-guard",
            abortSignal: params.abortSignal,
          },
        );
        const guardResults = parseTypesenseMultiSearchResults<object>(
          raw,
          chunk.length,
        );
        for (const result of guardResults) assertStableCandidateGuard(result);
      }
    };
    if (stableNewestReady) await guardStableCandidateOrder();
    for (let offset = 0; offset < plan.length; offset += MULTI_SEARCH_CHUNK_SIZE) {
      const chunk = plan.slice(offset, offset + MULTI_SEARCH_CHUNK_SIZE);
      const raw = await withTypesenseRetry(
        () =>
          client.multiSearch.perform(
            { searches: chunk.map((entry) => entry.search) },
            {},
            { abortSignal: params.abortSignal },
          ),
        {
          label: "matchCompiledWatchlistsInWindow",
          abortSignal: params.abortSignal,
        },
      );
      results.push(
        ...parseTypesenseMultiSearchResults<object>(raw, chunk.length, {
          expectHitsAt: chunk.map((_entry, index) => index),
        }),
      );
    }
    if (stableNewestReady) await guardStableCandidateOrder();
  }

  const hitsByWatchlist = params.watchlists.map(
    () => [] as RankedCandidateHit[],
  );
  const totals = params.watchlists.map(() => 0);
  results.forEach((result, resultIndex) => {
    const watchlistIndex = plan[resultIndex]!.watchlistIndex;
    totals[watchlistIndex] += result.found;
    hitsByWatchlist[watchlistIndex].push(
      ...(result.hits ?? []).map((hit, hitRank) => ({
        hit,
        batchIndex: plan[resultIndex]!.batchIndex,
        hitRank,
      })),
    );
  });

  const postings = new Map<string, MatchedWatchlistPosting>();
  const watchlistStats = params.watchlists.map((watchlist, index) => {
    const uniqueHits = new Map<string, RankedCandidateHit>();
    for (const rankedHit of hitsByWatchlist[index]) {
      const doc = rankedHit.hit.document as Record<string, unknown>;
      if (typeof doc.id !== "string") throw malformedTypesenseResponseError();
      if (!uniqueHits.has(doc.id)) uniqueHits.set(doc.id, rankedHit);
    }
    const selected = [...uniqueHits.values()]
      .sort((a, b) =>
        compareRankedHits(a, b, "newest", stableNewestReady)
      )
      .slice(0, params.limitPerWatchlist);
    for (const { hit } of selected) {
      const posting = mapCandidateHit(hit, stableNewestReady);
      const label = {
        id: watchlist.watchlistId,
        label: watchlist.watchlistLabel,
      };
      const existing = postings.get(posting.id);
      if (existing) existing.matchedWatchlists.push(label);
      else postings.set(posting.id, { ...posting, matchedWatchlists: [label] });
    }
    return {
      id: watchlist.watchlistId,
      label: watchlist.watchlistLabel,
      total: totals[index],
      returned: selected.length,
      truncated: totals[index] > selected.length,
    };
  });

  return {
    window: {
      windowStart: params.windowStart.toISOString(),
      windowEnd: params.windowEnd.toISOString(),
      boundary: WATCHLIST_CANDIDATE_WINDOW_BOUNDARY,
    },
    postings: [...postings.values()].sort((a, b) => {
      const freshness =
        Date.parse(b.firstSeenAt) - Date.parse(a.firstSeenAt);
      return freshness || canonicalStringCompare(a.id, b.id);
    }),
    watchlists: watchlistStats,
  };
}

/** Compile persisted filters and evaluate them through the same reader. */
export async function matchWatchlistsInWindow(params: {
  watchlists: readonly WatchlistMatcherSource[];
  windowStart: Date;
  windowEnd: Date;
  limitPerWatchlist: number;
  requireStableOrder?: boolean;
  abortSignal?: AbortSignal;
}): Promise<WatchlistWindowMatchResult> {
  const compiled = await compileWatchlistMatcherSources(params.watchlists);
  return matchCompiledWatchlistsInWindow({
    watchlists: compiled,
    windowStart: params.windowStart,
    windowEnd: params.windowEnd,
    limitPerWatchlist: params.limitPerWatchlist,
    requireStableOrder: params.requireStableOrder,
    abortSignal: params.abortSignal,
  });
}
