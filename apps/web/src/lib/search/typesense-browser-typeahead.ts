import { getTypesenseBrowserConfig, type TypesenseBrowserConfig } from "./typesense-browser-key";
import { buildFilterString } from "./typesense-filters";
import type { TypeaheadBoostFilters } from "./typeahead-boost";

export interface LocationSuggestion {
  id: number;
  slug: string;
  name: string;
  type: "macro" | "country" | "region" | "city";
  parentName: string | null;
}

export interface TaxonomySuggestion {
  id: number;
  slug: string;
  name: string;
  matchedName?: string;
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
  const filterParts = ["is_active:true", `${facetField}:[${ids.join(",")}]`];
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
    if (!r.hits || r.hits.length === 0) return [];
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

export async function suggestOccupationsBrowser(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  const { locale } = params;
  try {
    const cfg = await getTypesenseBrowserConfig();
    let r = await searchOne<OccupationDoc>(cfg, "occupation", {
      q,
      query_by: "name,aliases",
      filter_by: `has_active_postings:true && locale:${locale}`,
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "1",
    });
    if ((!r.hits || r.hits.length === 0) && locale !== "en") {
      r = await searchOne<OccupationDoc>(cfg, "occupation", {
        q,
        query_by: "name,aliases",
        filter_by: "has_active_postings:true && locale:en",
        sort_by: "_text_match:desc,active_posting_count:desc",
        per_page: 5,
        prefix: "true",
        num_typos: "1",
      });
    }
    if (!r.hits || r.hits.length === 0) return [];
    const suggestions: TaxonomySuggestion[] = r.hits.map((hit) => ({
      id: hit.document.occupation_id,
      slug: hit.document.slug,
      name: hit.document.name,
      matchedName: mapAliasMatch(hit, hit.document.name),
    }));
    if (!params.filters) return suggestions;
    return boost(cfg, suggestions, "occupation_id", (s) => s.id, params.filters);
  } catch {
    return [];
  }
}

export async function suggestSenioritiesBrowser(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  const { locale } = params;
  try {
    const cfg = await getTypesenseBrowserConfig();
    let r = await searchOne<SeniorityDoc>(cfg, "seniority", {
      q,
      query_by: "name,aliases",
      filter_by: `has_active_postings:true && locale:${locale}`,
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "1",
    });
    if ((!r.hits || r.hits.length === 0) && locale !== "en") {
      r = await searchOne<SeniorityDoc>(cfg, "seniority", {
        q,
        query_by: "name,aliases",
        filter_by: "has_active_postings:true && locale:en",
        sort_by: "_text_match:desc,active_posting_count:desc",
        per_page: 5,
        prefix: "true",
        num_typos: "1",
      });
    }
    if (!r.hits || r.hits.length === 0) return [];
    const suggestions: TaxonomySuggestion[] = r.hits.map((hit) => ({
      id: hit.document.seniority_id,
      slug: hit.document.slug,
      name: hit.document.name,
      matchedName: mapAliasMatch(hit, hit.document.name),
    }));
    if (!params.filters) return suggestions;
    return boost(cfg, suggestions, "seniority_id", (s) => s.id, params.filters);
  } catch {
    return [];
  }
}

export async function suggestTechnologiesBrowser(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

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
    if (!r.hits || r.hits.length === 0) return [];
    const suggestions: TaxonomySuggestion[] = r.hits.map((hit) => ({
      id: hit.document.technology_id,
      slug: hit.document.slug,
      name: hit.document.name ?? hit.document.slug,
    }));
    if (!params.filters) return suggestions;
    return boost(cfg, suggestions, "technology_ids", (s) => s.id, params.filters);
  } catch {
    return [];
  }
}
