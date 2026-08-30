import {
  getTypesenseBrowserConfig,
  invalidateTypesenseBrowserConfigIfUnauthorized,
  type TypesenseBrowserConfig,
} from "./typesense-browser-key";
import { buildFilterString, POSTING_BASE_FILTER } from "./typesense-filters";
import type { TypeaheadBoostFilters } from "./typeahead-boost";
import type { LocationSuggestion } from "@/lib/actions/locations";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import type { CompanySuggestion } from "@/lib/services/company";
import type {
  SearchBarTypeaheadParams,
  SearchBarTypeaheadResults,
} from "./typeahead-contract";

export type { LocationSuggestion, TaxonomySuggestion };

/**
 * Tiny LRU cache for typeahead results. Replaces the 1h server-side
 * `cached()` wrapper that the original `suggest*` server actions had —
 * direct browser->Typesense bypasses Redis, so without this every keystroke
 * cycle re-queries even when the user is just backspacing into a previous
 * stroke. TTL kept short (the data changes hourly via taxonomy sync); cap
 * keeps memory bounded across long-lived tabs.
 */
const SUGGEST_CACHE_TTL_MS = 60_000;
const SUGGEST_CACHE_MAX = 80;
const suggestCache = new Map<string, { at: number; value: unknown }>();

function cacheGet<T>(key: string): T | null {
  const entry = suggestCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.at > SUGGEST_CACHE_TTL_MS) {
    suggestCache.delete(key);
    return null;
  }
  // LRU: move to end on hit
  suggestCache.delete(key);
  suggestCache.set(key, entry);
  return entry.value as T;
}

function cacheSet<T>(key: string, value: T): void {
  if (suggestCache.has(key)) suggestCache.delete(key);
  suggestCache.set(key, { at: Date.now(), value });
  while (suggestCache.size > SUGGEST_CACHE_MAX) {
    const firstKey = suggestCache.keys().next().value;
    if (firstKey === undefined) break;
    suggestCache.delete(firstKey);
  }
}

function suggestCacheKey(kind: string, ...parts: (string | number | undefined)[]): string {
  return `${kind}:${parts.map((p) => p ?? "").join("|")}`;
}

interface SearchHit<T> {
  document: T;
  highlights?: Array<{ field: string; snippets?: string[] }>;
}

interface RawSearchResponse<T> {
  hits?: SearchHit<T>[];
  facet_counts?: Array<{
    field_name: string;
    counts: Array<{ value: string; count: number }>;
  }>;
  error?: string;
  code?: number;
}

interface MultiSearchRequest {
  collection: string;
  [key: string]: unknown;
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
  if (!res.ok) {
    invalidateTypesenseBrowserConfigIfUnauthorized(res.status);
    throw new Error(`typesense ${collection} ${res.status}`);
  }
  return res.json();
}

async function searchMany(
  cfg: TypesenseBrowserConfig,
  searches: MultiSearchRequest[],
): Promise<RawSearchResponse<Record<string, unknown>>[]> {
  if (searches.length === 0) return [];
  const url = `${cfg.protocol}://${cfg.host}:${cfg.port}/multi_search`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-typesense-api-key": cfg.apiKey,
    },
    body: JSON.stringify({ searches }),
  });
  if (!res.ok) {
    invalidateTypesenseBrowserConfigIfUnauthorized(res.status);
    throw new Error(`typesense multi_search ${res.status}`);
  }
  const body = (await res.json()) as {
    results?: RawSearchResponse<Record<string, unknown>>[];
  };
  if (!body.results || body.results.length !== searches.length) {
    throw new Error("typesense multi_search returned an incomplete result set");
  }
  return body.results;
}

async function boost<T>(
  cfg: TypesenseBrowserConfig,
  candidates: T[],
  facetField: string,
  idOf: (c: T) => number | string,
  filters: TypeaheadBoostFilters,
): Promise<T[]> {
  if (candidates.length === 0) return candidates;
  const filterStr = buildFilterString(filters);
  const hasKeywords = filters.keywords && filters.keywords.length > 0;
  if (!filterStr && !hasKeywords) return candidates;

  const ids = candidates.map(idOf);
  const filterParts = [POSTING_BASE_FILTER, `${facetField}:[${ids.join(",")}]`];
  if (filterStr) filterParts.push(filterStr);
  const q = hasKeywords ? filters.keywords!.join(" ") : "*";

  try {
    const r = await searchOne<unknown>(cfg, "job_posting", {
      q,
      query_by: "title",
      filter_by: filterParts.join(" && "),
      facet_by: facetField,
      facet_strategy: "exhaustive",
      max_facet_values: ids.length,
      per_page: 0,
    });
    const facet = r.facet_counts?.find((f) => f.field_name === facetField);
    if (!facet) return candidates;
    const matched = new Set<string>();
    for (const fc of facet.counts) if (fc.count > 0) matched.add(String(fc.value));
    const withMatches: T[] = [];
    const withoutMatches: T[] = [];
    for (const c of candidates) {
      if (matched.has(String(idOf(c)))) withMatches.push(c);
      else withoutMatches.push(c);
    }
    return [...withMatches, ...withoutMatches];
  } catch {
    return candidates;
  }
}

interface LocationDoc {
  location_id: number;
  slug: string;
  type: string;
  parent_name?: string;
  aliases?: string[];
  [key: `name_${string}`]: string | undefined;
}

export async function suggestLocationsBrowser(params: {
  query: string;
  locale: string;
  userLat?: number;
  userLng?: number;
  filters?: TypeaheadBoostFilters;
}): Promise<LocationSuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  const { locale, userLat, userLng } = params;

  // Cache the un-boosted suggestions only — boost depends on the live filter
  // set and would multiply cache keys. Boost runs after cache hit too.
  const cacheKey = suggestCacheKey(
    "loc",
    q,
    locale,
    userLat != null ? Math.round(userLat * 100) / 100 : undefined,
    userLng != null ? Math.round(userLng * 100) / 100 : undefined,
  );
  const hit = cacheGet<LocationSuggestion[]>(cacheKey);
  if (hit) {
    if (!params.filters) return hit;
    try {
      const cfg = await getTypesenseBrowserConfig();
      return await boost(cfg, hit, "location_ids", (s) => s.id, params.filters);
    } catch {
      return hit;
    }
  }
  const hasGeo = userLat != null && userLng != null;
  const sortBy = hasGeo
    ? `_text_match:desc,coordinates(${userLat},${userLng}, precision: 5km):asc,active_posting_count:desc`
    : "_text_match:desc,active_posting_count:desc";
  // ``aliases`` carries natural-language synonyms for macro-region rows
  // (e.g. EU's aliases include "European Union", "Europe"). Weighted
  // below the canonical name fields so an exact ``name_*`` prefix still
  // wins over an alias prefix on the same character. See #2939.
  const queryByFields =
    locale !== "en" ? `name_${locale},name_en,aliases` : "name_en,aliases";
  const queryByWeights = locale !== "en" ? "3,2,1" : "2,1";

  try {
    const cfg = await getTypesenseBrowserConfig();
    const r = await searchOne<LocationDoc>(cfg, "location", {
      q,
      query_by: queryByFields,
      query_by_weights: queryByWeights,
      filter_by: "has_active_postings:true",
      sort_by: sortBy,
      per_page: 8,
      prefix: "true",
      num_typos: "1",
      drop_tokens_threshold: 0,
    });
    if (!r.hits || r.hits.length === 0) {
      cacheSet(cacheKey, []);
      return [];
    }
    const suggestions: LocationSuggestion[] = r.hits.map((hit) => {
      const d = hit.document;
      return {
        id: d.location_id,
        slug: d.slug,
        name: (d[`name_${locale}`] ?? d.name_en ?? d.slug) as string,
        type: d.type as LocationSuggestion["type"],
        parentName: d.parent_name ?? null,
      };
    });
    cacheSet(cacheKey, suggestions);
    if (!params.filters) return suggestions;
    return boost(cfg, suggestions, "location_ids", (s) => s.id, params.filters);
  } catch {
    return [];
  }
}

interface OccupationDoc {
  occupation_id: number;
  slug: string;
  name: string;
}

interface SeniorityDoc {
  seniority_id: number;
  slug: string;
  name: string;
}

interface TechnologyDoc {
  technology_id: number;
  slug: string;
  name?: string;
}

interface CompanyDoc {
  id: string;
  name: string;
  slug: string;
  icon?: string | null;
}

function mapAliasMatch(
  hit: SearchHit<unknown>,
  displayName: string,
): string | undefined {
  const ah = hit.highlights?.find((h) => h.field === "aliases");
  const matched = ah?.snippets?.[0]?.replace(/<\/?mark>/g, "");
  return matched && matched !== displayName ? matched : undefined;
}

function requireSearchResult<T>(
  result: RawSearchResponse<T>,
  kind: string,
): RawSearchResponse<T> {
  if (result.error) {
    throw new Error(`typesense ${kind}: ${result.error}`);
  }
  return result;
}

function makeBoostSearch<T extends { id: number }>(
  candidates: T[],
  facetField: string,
  filters: TypeaheadBoostFilters | undefined,
): MultiSearchRequest | null {
  if (!filters || candidates.length === 0) return null;
  const filterStr = buildFilterString(filters);
  const hasKeywords = filters.keywords && filters.keywords.length > 0;
  if (!filterStr && !hasKeywords) return null;

  const ids = candidates.map((candidate) => candidate.id);
  const filterParts = [POSTING_BASE_FILTER, `${facetField}:[${ids.join(",")}]`];
  if (filterStr) filterParts.push(filterStr);
  return {
    collection: "job_posting",
    q: hasKeywords ? filters.keywords!.join(" ") : "*",
    query_by: "title",
    filter_by: filterParts.join(" && "),
    facet_by: facetField,
    facet_strategy: "exhaustive",
    max_facet_values: ids.length,
    per_page: 0,
  };
}

function applyBoostResult<T extends { id: number }>(
  candidates: T[],
  facetField: string,
  result: RawSearchResponse<Record<string, unknown>>,
): T[] {
  // Boosting is an optional ranking refinement. Match the legacy direct
  // path by retaining candidate order when this individual facet fails.
  if (result.error) return candidates;
  const facet = result.facet_counts?.find((entry) => entry.field_name === facetField);
  if (!facet) return candidates;
  const matched = new Set(
    facet.counts.filter((entry) => entry.count > 0).map((entry) => entry.value),
  );
  return [
    ...candidates.filter((candidate) => matched.has(String(candidate.id))),
    ...candidates.filter((candidate) => !matched.has(String(candidate.id))),
  ];
}

/**
 * Batched direct-search implementation for the shared search bar.
 *
 * Candidate collections share one `multi_search`; non-English fallback
 * collections share a second request only when needed; all posting-count
 * ranking facets share a final request. The module-level LRUs remain the
 * same slots used by the individual typeahead functions below.
 */
export async function suggestSearchBarBrowser(
  params: SearchBarTypeaheadParams,
): Promise<SearchBarTypeaheadResults> {
  const q = params.query.trim();
  if (q.length < 2) {
    return {
      locations: [],
      companies: [],
      occupations: [],
      seniorities: [],
      technologies: [],
    };
  }

  const locationKey = suggestCacheKey(
    "loc",
    q,
    params.locale,
    params.userLat != null ? Math.round(params.userLat * 100) / 100 : undefined,
    params.userLng != null ? Math.round(params.userLng * 100) / 100 : undefined,
  );
  const companyKey = suggestCacheKey("company", q);
  const occupationKey = suggestCacheKey("occ", q, params.locale);
  const seniorityKey = suggestCacheKey("sen", q, params.locale);
  const technologyKey = suggestCacheKey("tech", q);

  const cachedLocations = cacheGet<LocationSuggestion[]>(locationKey);
  const cachedCompanies = params.includeCompanies
    ? cacheGet<CompanySuggestion[]>(companyKey)
    : [];
  const cachedOccupations = cacheGet<TaxonomySuggestion[]>(occupationKey);
  const cachedSeniorities = cacheGet<TaxonomySuggestion[]>(seniorityKey);
  const cachedTechnologies = cacheGet<TaxonomySuggestion[]>(technologyKey);

  let locations = cachedLocations;
  let companies = cachedCompanies;
  let occupations = cachedOccupations;
  let seniorities = cachedSeniorities;
  let technologies = cachedTechnologies;

  const hasGeo = params.userLat != null && params.userLng != null;
  const locationSort = hasGeo
    ? `_text_match:desc,coordinates(${params.userLat},${params.userLng}, precision: 5km):asc,active_posting_count:desc`
    : "_text_match:desc,active_posting_count:desc";
  const locationQueryBy =
    params.locale !== "en" ? `name_${params.locale},name_en,aliases` : "name_en,aliases";
  const locationWeights = params.locale !== "en" ? "3,2,1" : "2,1";
  const localeSearch = {
    q,
    query_by: "name,aliases",
    filter_by: `has_active_postings:true && locale:${params.locale}`,
    sort_by: "_text_match:desc,active_posting_count:desc",
    per_page: 5,
    prefix: "true",
    num_typos: "1",
  };

  type CandidateKind = "location" | "company" | "occupation" | "seniority" | "technology";
  const candidateKinds: CandidateKind[] = [];
  const candidateSearches: MultiSearchRequest[] = [];
  const addCandidate = (kind: CandidateKind, search: MultiSearchRequest) => {
    candidateKinds.push(kind);
    candidateSearches.push(search);
  };

  if (locations === null) {
    addCandidate("location", {
      collection: "location",
      q,
      query_by: locationQueryBy,
      query_by_weights: locationWeights,
      filter_by: "has_active_postings:true",
      sort_by: locationSort,
      per_page: 8,
      prefix: "true",
      num_typos: "1",
      drop_tokens_threshold: 0,
    });
  }
  if (params.includeCompanies && companies === null) {
    addCandidate("company", {
      collection: "company",
      q,
      query_by: "name",
      filter_by: "active_posting_count:>0",
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: true,
      num_typos: 1,
    });
  }
  if (occupations === null) {
    addCandidate("occupation", { collection: "occupation", ...localeSearch });
  }
  if (seniorities === null) {
    addCandidate("seniority", { collection: "seniority", ...localeSearch });
  }
  if (technologies === null) {
    addCandidate("technology", {
      collection: "technology",
      q,
      query_by: "name,slug",
      filter_by: "has_active_postings:true",
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "0",
    });
  }

  let cfg: TypesenseBrowserConfig | null = null;
  const getConfig = async () => {
    cfg ??= await getTypesenseBrowserConfig();
    return cfg;
  };
  const candidateResults = candidateSearches.length
    ? await searchMany(await getConfig(), candidateSearches)
    : [];
  for (let index = 0; index < candidateKinds.length; index += 1) {
    const kind = candidateKinds[index];
    const result = requireSearchResult(candidateResults[index], kind);
    if (kind === "location") {
      locations = (result.hits ?? []).map((hit) => {
        const doc = hit.document as unknown as LocationDoc;
        return {
          id: doc.location_id,
          slug: doc.slug,
          name: (doc[`name_${params.locale}`] ?? doc.name_en ?? doc.slug) as string,
          type: doc.type as LocationSuggestion["type"],
          parentName: doc.parent_name ?? null,
        };
      });
      cacheSet(locationKey, locations);
    } else if (kind === "company") {
      companies = (result.hits ?? []).map((hit) => {
        const doc = hit.document as unknown as CompanyDoc;
        return {
          id: String(doc.id),
          name: String(doc.name ?? ""),
          slug: String(doc.slug ?? ""),
          icon: typeof doc.icon === "string" ? doc.icon : null,
        };
      });
      cacheSet(companyKey, companies);
    } else if (kind === "occupation" || kind === "seniority") {
      const mapped = (result.hits ?? []).map((hit) => {
        const doc = hit.document as unknown as OccupationDoc | SeniorityDoc;
        return {
          id: "occupation_id" in doc ? doc.occupation_id : doc.seniority_id,
          slug: doc.slug,
          name: doc.name,
          matchedName: mapAliasMatch(hit as SearchHit<unknown>, doc.name),
        };
      });
      if (kind === "occupation") occupations = mapped;
      else seniorities = mapped;
    } else {
      technologies = (result.hits ?? []).map((hit) => {
        const doc = hit.document as unknown as TechnologyDoc;
        return {
          id: doc.technology_id,
          slug: doc.slug,
          name: doc.name ?? doc.slug,
        };
      });
      cacheSet(technologyKey, technologies);
    }
  }

  // The locale-aware cache stores the final fallback result, not the empty
  // first pass, so subsequent queries do not repeat the fallback phase.
  type FallbackKind = "occupation" | "seniority";
  const fallbackKinds: FallbackKind[] = [];
  const fallbackSearches: MultiSearchRequest[] = [];
  const englishFallback = {
    ...localeSearch,
    filter_by: "has_active_postings:true && locale:en",
  };
  if (cachedOccupations === null && occupations?.length === 0 && params.locale !== "en") {
    fallbackKinds.push("occupation");
    fallbackSearches.push({ collection: "occupation", ...englishFallback });
  }
  if (cachedSeniorities === null && seniorities?.length === 0 && params.locale !== "en") {
    fallbackKinds.push("seniority");
    fallbackSearches.push({ collection: "seniority", ...englishFallback });
  }
  const fallbackResults = fallbackSearches.length
    ? await searchMany(await getConfig(), fallbackSearches)
    : [];
  for (let index = 0; index < fallbackKinds.length; index += 1) {
    const kind = fallbackKinds[index];
    const result = requireSearchResult(fallbackResults[index], `${kind} locale fallback`);
    const mapped = (result.hits ?? []).map((hit) => {
      const doc = hit.document as unknown as OccupationDoc | SeniorityDoc;
      return {
        id: "occupation_id" in doc ? doc.occupation_id : doc.seniority_id,
        slug: doc.slug,
        name: doc.name,
        matchedName: mapAliasMatch(hit as SearchHit<unknown>, doc.name),
      };
    });
    if (kind === "occupation") occupations = mapped;
    else seniorities = mapped;
  }
  if (cachedOccupations === null) cacheSet(occupationKey, occupations ?? []);
  if (cachedSeniorities === null) cacheSet(seniorityKey, seniorities ?? []);

  type BoostKind = "location" | "occupation" | "seniority" | "technology";
  const boostKinds: BoostKind[] = [];
  const boostSearches: MultiSearchRequest[] = [];
  const addBoost = (
    kind: BoostKind,
    candidates: Array<{ id: number }>,
    facetField: string,
    filters: TypeaheadBoostFilters | undefined,
  ) => {
    const search = makeBoostSearch(candidates, facetField, filters);
    if (!search) return;
    boostKinds.push(kind);
    boostSearches.push(search);
  };
  addBoost("location", locations ?? [], "location_ids", params.locationFilters);
  addBoost("occupation", occupations ?? [], "occupation_id", params.occupationFilters);
  addBoost("seniority", seniorities ?? [], "seniority_id", params.seniorityFilters);
  addBoost("technology", technologies ?? [], "technology_ids", params.technologyFilters);

  try {
    const boostResults = boostSearches.length
      ? await searchMany(await getConfig(), boostSearches)
      : [];
    for (let index = 0; index < boostKinds.length; index += 1) {
      const kind = boostKinds[index];
      if (kind === "location") {
        locations = applyBoostResult(locations ?? [], "location_ids", boostResults[index]);
      } else if (kind === "occupation") {
        occupations = applyBoostResult(occupations ?? [], "occupation_id", boostResults[index]);
      } else if (kind === "seniority") {
        seniorities = applyBoostResult(seniorities ?? [], "seniority_id", boostResults[index]);
      } else {
        technologies = applyBoostResult(technologies ?? [], "technology_ids", boostResults[index]);
      }
    }
  } catch {
    // Posting-count boosts are best-effort. Candidate relevance remains
    // correct, so retain it rather than falling back and exceeding budget.
  }

  return {
    locations: locations ?? [],
    companies: companies ?? [],
    occupations: occupations ?? [],
    seniorities: seniorities ?? [],
    technologies: technologies ?? [],
  };
}

interface LocaleAwareDoc {
  slug: string;
  name: string;
}

async function suggestLocaleAware<D extends LocaleAwareDoc>(opts: {
  collection: string;
  locale: string;
  query: string;
  filters?: TypeaheadBoostFilters;
  facetField: string;
  idOf: (d: D) => number;
  cacheKind: string;
}): Promise<TaxonomySuggestion[]> {
  const cacheKey = suggestCacheKey(opts.cacheKind, opts.query, opts.locale);
  const hit = cacheGet<TaxonomySuggestion[]>(cacheKey);
  const finalize = async (
    cfg: TypesenseBrowserConfig,
    suggestions: TaxonomySuggestion[],
  ): Promise<TaxonomySuggestion[]> => {
    if (!opts.filters) return suggestions;
    return boost(cfg, suggestions, opts.facetField, (s) => s.id, opts.filters);
  };
  if (hit) {
    if (!opts.filters) return hit;
    try {
      const cfg = await getTypesenseBrowserConfig();
      return await finalize(cfg, hit);
    } catch {
      return hit;
    }
  }

  try {
    const cfg = await getTypesenseBrowserConfig();
    const baseParams = {
      q: opts.query,
      query_by: "name,aliases",
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "1",
    };
    let r = await searchOne<D>(cfg, opts.collection, {
      ...baseParams,
      filter_by: `has_active_postings:true && locale:${opts.locale}`,
    });
    if ((!r.hits || r.hits.length === 0) && opts.locale !== "en") {
      r = await searchOne<D>(cfg, opts.collection, {
        ...baseParams,
        filter_by: "has_active_postings:true && locale:en",
      });
    }
    if (!r.hits || r.hits.length === 0) {
      cacheSet(cacheKey, []);
      return [];
    }
    const suggestions: TaxonomySuggestion[] = r.hits.map((h) => ({
      id: opts.idOf(h.document),
      slug: h.document.slug,
      name: h.document.name,
      matchedName: mapAliasMatch(h, h.document.name),
    }));
    cacheSet(cacheKey, suggestions);
    return finalize(cfg, suggestions);
  } catch {
    return [];
  }
}

export async function suggestOccupationsBrowser(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];
  return suggestLocaleAware<OccupationDoc>({
    collection: "occupation",
    locale: params.locale,
    query: q,
    filters: params.filters,
    facetField: "occupation_id",
    idOf: (d) => d.occupation_id,
    cacheKind: "occ",
  });
}

export async function suggestSenioritiesBrowser(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];
  return suggestLocaleAware<SeniorityDoc>({
    collection: "seniority",
    locale: params.locale,
    query: q,
    filters: params.filters,
    facetField: "seniority_id",
    idOf: (d) => d.seniority_id,
    cacheKind: "sen",
  });
}

export async function suggestTechnologiesBrowser(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  const cacheKey = suggestCacheKey("tech", q);
  const hit = cacheGet<TaxonomySuggestion[]>(cacheKey);
  if (hit) {
    if (!params.filters) return hit;
    try {
      const cfg = await getTypesenseBrowserConfig();
      return await boost(cfg, hit, "technology_ids", (s) => s.id, params.filters);
    } catch {
      return hit;
    }
  }

  try {
    const cfg = await getTypesenseBrowserConfig();
    const r = await searchOne<TechnologyDoc>(cfg, "technology", {
      q,
      query_by: "name,slug",
      filter_by: "has_active_postings:true",
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "0",
    });
    if (!r.hits || r.hits.length === 0) {
      cacheSet(cacheKey, []);
      return [];
    }
    const suggestions: TaxonomySuggestion[] = r.hits.map((hit) => ({
      id: hit.document.technology_id,
      slug: hit.document.slug,
      name: hit.document.name ?? hit.document.slug,
    }));
    cacheSet(cacheKey, suggestions);
    if (!params.filters) return suggestions;
    return boost(cfg, suggestions, "technology_ids", (s) => s.id, params.filters);
  } catch {
    return [];
  }
}
