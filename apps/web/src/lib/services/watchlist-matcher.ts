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
} from "@/lib/search/typesense-query-size";
import { COMPANY_BATCH_SIZE } from "@/lib/search/constants";
import { normalizePostingTitle } from "@/lib/posting-title";
import { canonicalStringCompare } from "@/lib/sort";
import {
  buildWatchlistCandidateSearchParams,
  hasWatchlistCandidateScope,
  WATCHLIST_CANDIDATE_WINDOW_BOUNDARY,
  type WatchlistCandidateOrder,
  type WatchlistCandidateSearchParams,
  type WatchlistCandidateWindow,
} from "@/lib/search/watchlist-candidate-query";
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

function mapCandidateHit(hit: CandidateHit): WatchlistPostingEntry {
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

function compareHits(
  a: CandidateHit,
  b: CandidateHit,
  order: WatchlistCandidateOrder,
): number {
  if (order === "interactive") {
    const relevance = (b.text_match ?? 0) - (a.text_match ?? 0);
    if (relevance !== 0) return relevance;
  }
  const aDoc = a.document as Record<string, unknown>;
  const bDoc = b.document as Record<string, unknown>;
  const freshness =
    ((bDoc.first_seen_at as number) ?? 0) -
    ((aDoc.first_seen_at as number) ?? 0);
  if (freshness !== 0) return freshness;
  return canonicalStringCompare(String(aDoc.id ?? ""), String(bDoc.id ?? ""));
}

function batchesForFilters(
  filters: WatchlistCandidateFilters,
  buildParams: (filters: WatchlistCandidateFilters) => WatchlistCandidateSearchParams,
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
  abortSignal?: AbortSignal;
}): Promise<{ postings: WatchlistPostingEntry[]; total: number }> {
  if (!hasWatchlistCandidateScope(params.filters)) {
    return { postings: [], total: 0 };
  }
  const order = params.order ?? "interactive";
  const buildParams = (filters: WatchlistCandidateFilters) =>
    buildWatchlistCandidateSearchParams({
      filters,
      offset: params.offset,
      limit: params.limit,
      window: params.window,
      order,
    });
  const searchParams = buildParams(params.filters);
  const needsBatches =
    !params.filters.anyCompany &&
    params.filters.companyIds.length > 0 &&
    (params.filters.companyIds.length > COMPANY_BATCH_SIZE ||
      !isTypesenseQueryStringSafe(searchParams));
  const client = getSearchClient();
  if (!needsBatches) {
    const result = await withTypesenseRetry(
      () =>
        client.collections("job_posting").documents().search(searchParams, {
          abortSignal: params.abortSignal,
        }),
      { label: "readWatchlistCandidates", abortSignal: params.abortSignal },
    );
    assertTypesenseSearchResult(result, { expectHits: params.limit !== 0 });
    const total = result.found ?? 0;
    return {
      postings:
        total === 0 || params.limit === 0
          ? []
          : (result.hits ?? []).map(mapCandidateHit),
      total,
    };
  }

  const needed = params.offset + params.limit;
  const filterBatches = batchesForFilters(params.filters, (filters) =>
    buildWatchlistCandidateSearchParams({
      filters,
      offset: 0,
      limit: needed,
      window: params.window,
      order,
    }),
  );
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
  if (total === 0 || params.limit === 0) return { postings: [], total };

  const rowResults = await Promise.all(
    filterBatches.map((filters) =>
      withTypesenseRetry(
        () =>
          client.collections("job_posting").documents().search(
            buildWatchlistCandidateSearchParams({
              filters,
              offset: 0,
              limit: needed,
              window: params.window,
              order,
            }),
            { abortSignal: params.abortSignal },
          ),
        {
          label: "readWatchlistCandidates.batched.rows",
          abortSignal: params.abortSignal,
        },
      ),
    ),
  );
  for (const result of rowResults) {
    assertTypesenseSearchResult(result, { expectHits: true });
  }
  const allHits = rowResults.flatMap((result) => result.hits ?? []);
  allHits.sort((a, b) => compareHits(a, b, order));
  return {
    postings: allHits
      .slice(params.offset, params.offset + params.limit)
      .map(mapCandidateHit),
    total,
  };
}

type SearchPlanEntry = {
  watchlistIndex: number;
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
  abortSignal?: AbortSignal;
}): Promise<WatchlistWindowMatchResult> {
  if (
    !Number.isInteger(params.limitPerWatchlist) ||
    params.limitPerWatchlist < 1 ||
    params.limitPerWatchlist > 250
  ) {
    throw new RangeError("limitPerWatchlist must be an integer between 1 and 250");
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
      }),
    );
    for (const filters of batches) {
      plan.push({
        watchlistIndex,
        search: {
          collection: "job_posting",
          ...buildWatchlistCandidateSearchParams({
            filters,
            offset: 0,
            limit: params.limitPerWatchlist,
            window,
            order: "newest",
          }),
        },
      });
    }
  });

  const results: TypesenseMultiSearchResult<object>[] = [];
  if (plan.length > 0) {
    const client = getSearchClient();
    for (let offset = 0; offset < plan.length; offset += MULTI_SEARCH_CHUNK_SIZE) {
      const chunk = plan.slice(offset, offset + MULTI_SEARCH_CHUNK_SIZE);
      const raw = await withTypesenseRetry(
        () =>
          client.multiSearch.perform({
            searches: chunk.map((entry) => entry.search),
          }),
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
  }

  const hitsByWatchlist = params.watchlists.map(() => [] as CandidateHit[]);
  const totals = params.watchlists.map(() => 0);
  results.forEach((result, resultIndex) => {
    const watchlistIndex = plan[resultIndex]!.watchlistIndex;
    totals[watchlistIndex] += result.found;
    hitsByWatchlist[watchlistIndex].push(...(result.hits ?? []));
  });

  const postings = new Map<string, MatchedWatchlistPosting>();
  const watchlistStats = params.watchlists.map((watchlist, index) => {
    const uniqueHits = new Map<string, CandidateHit>();
    for (const hit of hitsByWatchlist[index]) {
      const doc = hit.document as Record<string, unknown>;
      if (typeof doc.id !== "string") throw malformedTypesenseResponseError();
      if (!uniqueHits.has(doc.id)) uniqueHits.set(doc.id, hit);
    }
    const selected = [...uniqueHits.values()]
      .sort((a, b) => compareHits(a, b, "newest"))
      .slice(0, params.limitPerWatchlist);
    for (const hit of selected) {
      const posting = mapCandidateHit(hit);
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
  abortSignal?: AbortSignal;
}): Promise<WatchlistWindowMatchResult> {
  const compiled = await compileWatchlistMatcherSources(params.watchlists);
  return matchCompiledWatchlistsInWindow({
    watchlists: compiled,
    windowStart: params.windowStart,
    windowEnd: params.windowEnd,
    limitPerWatchlist: params.limitPerWatchlist,
    abortSignal: params.abortSignal,
  });
}
