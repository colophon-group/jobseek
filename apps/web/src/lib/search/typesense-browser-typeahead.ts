import { getTypesenseBrowserConfig, type TypesenseBrowserConfig } from "./typesense-browser-key";
import { buildFilterString, POSTING_BASE_FILTER } from "./typesense-filters";
import type { TypeaheadBoostFilters } from "./typeahead-boost";
import type { LocationSuggestion } from "@/lib/actions/locations";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";

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
  const queryByFields = locale !== "en" ? `name_${locale},name_en` : "name_en";
  const queryByWeights = locale !== "en" ? "3,1" : "1";

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

function mapAliasMatch(
  hit: SearchHit<unknown>,
  displayName: string,
): string | undefined {
  const ah = hit.highlights?.find((h) => h.field === "aliases");
  const matched = ah?.snippets?.[0]?.replace(/<\/?mark>/g, "");
  return matched && matched !== displayName ? matched : undefined;
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
