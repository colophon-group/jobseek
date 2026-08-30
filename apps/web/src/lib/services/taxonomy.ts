import "server-only";

import { cacheLife, cacheTag } from "next/cache";
import { cached } from "@/lib/cache";
import { CACHE_TTL_LONG } from "@/lib/cache-ttl";
import {
  typeaheadOccupationsCacheTag,
  typeaheadSenioritiesCacheTag,
  typeaheadTechnologiesCacheTag,
} from "@/lib/cache-tags";
import { getTypesenseClient, type TypesenseHit } from "@/lib/search/typesense-client";
import {
  isTypesenseUnavailableError,
  sanitizeTypesenseBoundaryError,
} from "@/lib/search/typesense-retry";
import {
  fetchOccupationDocuments,
  fetchSeniorityDocuments,
  fetchTechnologyDocuments,
} from "@/lib/search/typesense-taxonomy";
import { buildFilterString, POSTING_BASE_FILTER } from "@/lib/search/typesense-filters";
import { boostByFilterMatches, type TypeaheadBoostFilters } from "@/lib/search/typeahead-boost";
import { canonicalizeFilters } from "@/lib/search/canonicalize-filters";
import { canonicalStringCompare } from "@/lib/sort";
import { logExternalError } from "@/lib/safe-external-error";

export interface TaxonomySuggestion {
  id: number;
  slug: string;
  name: string;
  /** The alias that matched the query (if different from display name). */
  matchedName?: string;
}

// ── suggestOccupations (Typesense) ──────────────────────────────────

export async function suggestOccupations(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
  failOnUnavailable?: boolean;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  // Per-region in-memory `'use cache'`. See note on
  // `_fetchOccupationSuggestionsCached` below for null-vs-empty semantics.
  // Migrated from Redis-backed `cached()` in #2884 (typeaheads slice).
  let suggestions: TaxonomySuggestion[];
  try {
    suggestions = await _fetchOccupationSuggestionsCached(
      q.toLowerCase(),
      params.locale,
    );
  } catch (err) {
    if (params.failOnUnavailable) throw err;
    suggestions = [];
  }
  if (!params.filters) return suggestions;
  return boostByFilterMatches(
    suggestions,
    "occupation_id",
    (s) => s.id,
    params.filters,
  );
}

/**
 * Cached inner fetch + mapping for {@link suggestOccupations}. Throws if
 * Typesense is unreachable so the wrapper can swallow the error and avoid
 * pinning an outage-shaped empty list inside the `'use cache'` boundary.
 * Empty array is a legitimate "no match" result and IS cached.
 */
async function _fetchOccupationSuggestionsCached(
  q: string,
  locale: string,
): Promise<TaxonomySuggestion[]> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_LONG });
  // Tag the slot so `revalidateTag(typeaheadOccupationsCacheTag())` from
  // /api/internal/invalidate-typeahead drops it after `crawler sync`,
  // instead of waiting up to 3600s for the TTL. See #2907 follow-up.
  cacheTag(typeaheadOccupationsCacheTag());

  let result;
  try {
    const client = getTypesenseClient();

    // Search locale-specific documents first
    result = await client.collections("occupation").documents().search({
      q,
      query_by: "name,aliases",
      filter_by: `has_active_postings:true && locale:${locale}`,
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "1",
    });

    // Locale fallback: retry with locale:en if 0 results
    if ((!result.hits || result.hits.length === 0) && locale !== "en") {
      result = await client.collections("occupation").documents().search({
        q,
        query_by: "name,aliases",
        filter_by: "has_active_postings:true && locale:en",
        sort_by: "_text_match:desc,active_posting_count:desc",
        per_page: 5,
        prefix: "true",
        num_typos: "1",
      });
    }
  } catch (err) {
    // Throw past the cache boundary so the wrapper returns `[]` without
    // pinning the slot for the next 3600s.
    throw sanitizeTypesenseBoundaryError(err);
  }

  if (!result.hits || result.hits.length === 0) return [];
  return result.hits.map((hit) =>
    _mapOccupationHit(hit as unknown as TypesenseHit),
  );
}

function _mapOccupationHit(hit: TypesenseHit): TaxonomySuggestion {
  const doc = hit.document;
  const aliasHighlight = hit.highlights?.find((h) => h.field === "aliases");
  const matchedAlias = aliasHighlight?.snippets?.[0]?.replace(/<\/?mark>/g, "");

  return {
    id: doc.occupation_id as number,
    slug: doc.slug as string,
    name: doc.name as string,
    matchedName: matchedAlias && matchedAlias !== doc.name ? matchedAlias : undefined,
  };
}

// ── suggestSeniorities (Typesense) ──────────────────────────────────

export async function suggestSeniorities(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
  failOnUnavailable?: boolean;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  // Per-region in-memory `'use cache'`. See note on
  // `_fetchSenioritySuggestionsCached` below for null-vs-empty semantics.
  // Migrated from Redis-backed `cached()` in #2884 (typeaheads slice).
  let suggestions: TaxonomySuggestion[];
  try {
    suggestions = await _fetchSenioritySuggestionsCached(
      q.toLowerCase(),
      params.locale,
    );
  } catch (err) {
    if (params.failOnUnavailable) throw err;
    suggestions = [];
  }
  if (!params.filters) return suggestions;
  return boostByFilterMatches(
    suggestions,
    "seniority_id",
    (s) => s.id,
    params.filters,
  );
}

/**
 * Cached inner fetch + mapping for {@link suggestSeniorities}. Throws if
 * Typesense is unreachable so the wrapper can swallow the error and avoid
 * pinning an outage-shaped empty list inside the `'use cache'` boundary.
 * Empty array is a legitimate "no match" result and IS cached.
 */
async function _fetchSenioritySuggestionsCached(
  q: string,
  locale: string,
): Promise<TaxonomySuggestion[]> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_LONG });
  // Tag the slot so `revalidateTag(typeaheadSenioritiesCacheTag())` from
  // /api/internal/invalidate-typeahead drops it after `crawler sync`,
  // instead of waiting up to 3600s for the TTL. See #2907 follow-up.
  cacheTag(typeaheadSenioritiesCacheTag());

  let result;
  try {
    const client = getTypesenseClient();

    result = await client.collections("seniority").documents().search({
      q,
      query_by: "name,aliases",
      filter_by: `has_active_postings:true && locale:${locale}`,
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "1",
    });

    // Locale fallback: retry with locale:en if 0 results
    if ((!result.hits || result.hits.length === 0) && locale !== "en") {
      result = await client.collections("seniority").documents().search({
        q,
        query_by: "name,aliases",
        filter_by: "has_active_postings:true && locale:en",
        sort_by: "_text_match:desc,active_posting_count:desc",
        per_page: 5,
        prefix: "true",
        num_typos: "1",
      });
    }
  } catch (err) {
    // Throw past the cache boundary so the wrapper returns `[]` without
    // pinning the slot for the next 3600s.
    throw sanitizeTypesenseBoundaryError(err);
  }

  if (!result.hits || result.hits.length === 0) return [];
  return result.hits.map((hit) =>
    _mapSeniorityHit(hit as unknown as TypesenseHit),
  );
}

function _mapSeniorityHit(hit: TypesenseHit): TaxonomySuggestion {
  const doc = hit.document;
  const aliasHighlight = hit.highlights?.find((h) => h.field === "aliases");
  const matchedAlias = aliasHighlight?.snippets?.[0]?.replace(/<\/?mark>/g, "");

  return {
    id: doc.seniority_id as number,
    slug: doc.slug as string,
    name: doc.name as string,
    matchedName: matchedAlias && matchedAlias !== doc.name ? matchedAlias : undefined,
  };
}

// ── suggestTechnologies (Typesense) ─────────────────────────────────

export async function suggestTechnologies(params: {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
  failOnUnavailable?: boolean;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  // Technologies are locale-agnostic but the public function accepts locale
  // for parity with the other taxonomy suggesters. The inner cached fetcher
  // takes ONLY `q` so all locales share the same `'use cache'` slot — the
  // implicit argument-hash key drops `locale` because it isn't a parameter.
  // (#2884 footgun — was an explicit cache-key drop under the manual-key
  // `cached()` helper; under `'use cache'` we encode it via the function
  // signature instead.) Migrated from Redis-backed `cached()` in #2884.
  let suggestions: TaxonomySuggestion[];
  try {
    suggestions = await _fetchTechnologySuggestionsCached(q.toLowerCase());
  } catch (err) {
    if (params.failOnUnavailable) throw err;
    suggestions = [];
  }
  if (!params.filters) return suggestions;
  return boostByFilterMatches(
    suggestions,
    "technology_ids",
    (s) => s.id,
    params.filters,
  );
}

/**
 * Cached inner fetch + mapping for {@link suggestTechnologies}. Throws if
 * Typesense is unreachable so the wrapper can swallow the error and avoid
 * pinning an outage-shaped empty list inside the `'use cache'` boundary.
 * Empty array is a legitimate "no match" result and IS cached.
 *
 * Takes only `q` (no `locale` arg) — technologies are locale-agnostic, so
 * stripping locale from the cache-key inputs lets all locales share the
 * same slot. See note on the {@link suggestTechnologies} wrapper.
 */
async function _fetchTechnologySuggestionsCached(
  q: string,
): Promise<TaxonomySuggestion[]> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_LONG });
  // Tag the slot so `revalidateTag(typeaheadTechnologiesCacheTag())` from
  // /api/internal/invalidate-typeahead drops it after `crawler sync`,
  // instead of waiting up to 3600s for the TTL. See #2907 follow-up.
  cacheTag(typeaheadTechnologiesCacheTag());

  let result;
  try {
    const client = getTypesenseClient();

    result = await client.collections("technology").documents().search({
      q,
      query_by: "name,slug",
      filter_by: "has_active_postings:true",
      sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5,
      prefix: "true",
      num_typos: "0", // no typo tolerance — match current prefix-only behavior
    });
  } catch (err) {
    // Throw past the cache boundary so the wrapper returns `[]` without
    // pinning the slot for the next 3600s.
    throw sanitizeTypesenseBoundaryError(err);
  }

  if (!result.hits || result.hits.length === 0) return [];
  return result.hits.map((hit) => {
    const doc = (hit as unknown as TypesenseHit).document;
    return {
      id: doc.technology_id as number,
      slug: doc.slug as string,
      name: (doc.name ?? doc.slug) as string,
    };
  });
}

// ── Resolve functions (per-region in-memory `'use cache'`) ──────────
//
// Migrated from Redis-backed `cached()` in #2884 (resolve/expand slice,
// bucket 3). These translate slugs -> {id, slug, name} for filter chips
// on `/[lang]/explore` and watchlist pages — called per-render and
// stable across requests for the same slug set, so a `cacheLife('days')`
// per-region in-memory hit is the right shape (was 3600s on Redis).
//
// Cache-key shape under `'use cache'`: arguments are hashed by Next, so
// the wrapper sorts the slug array first to keep `[a,b]` and `[b,a]`
// in the same slot (the legacy manual-key path also sorted).
//
// Tagging: reuses the per-typeahead `cacheTag` namers so the existing
// `/api/internal/invalidate-typeahead` route, fired by `crawler sync`,
// drops resolve slots in the same sweep that drops the typeaheads —
// both surfaces share the taxonomy-rename trigger source.

export async function resolveOccupationSlugs(
  slugs: string[],
  locale: string,
  options: { abortSignal?: AbortSignal } = {},
): Promise<Map<string, TaxonomySuggestion>> {
  if (slugs.length === 0) return new Map();
  // `canonicalStringCompare` instead of raw `.sort()` so accented slugs
  // (e.g. a locale where occupation slugs include `é`) don't fragment the
  // `'use cache'` slot between callers passing the same logical input in
  // different orders. See #3276 (follow-up to #3221).
  const sorted = [...slugs].sort(canonicalStringCompare);
  if (options.abortSignal) {
    return new Map(
      Object.entries(
        await _resolveOccupationSlugs(sorted, locale, options.abortSignal),
      ),
    );
  }
  const record = await _resolveOccupationSlugsCached(sorted, locale);
  return new Map(Object.entries(record));
}

async function _resolveOccupationSlugsCached(
  sortedSlugs: string[],
  locale: string,
): Promise<Record<string, TaxonomySuggestion>> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadOccupationsCacheTag());

  return _resolveOccupationSlugs(sortedSlugs, locale);
}

async function _resolveOccupationSlugs(
  sortedSlugs: string[],
  locale: string,
  abortSignal?: AbortSignal,
): Promise<Record<string, TaxonomySuggestion>> {

  const rows = abortSignal
    ? await fetchOccupationDocuments(locale, abortSignal)
    : await fetchOccupationDocuments(locale);
  const wanted = new Set(sortedSlugs);
  const result: Record<string, TaxonomySuggestion> = {};
  for (const row of rows) {
    if (!wanted.has(row.slug)) continue;
    result[row.slug] = {
      id: row.occupation_id,
      slug: row.slug,
      name: row.name,
    };
  }
  return result;
}

export async function resolveSenioritySlugs(
  slugs: string[],
  locale: string,
  options: { abortSignal?: AbortSignal } = {},
): Promise<Map<string, TaxonomySuggestion>> {
  if (slugs.length === 0) return new Map();
  // See `resolveOccupationSlugs` for the canonicalization rationale.
  const sorted = [...slugs].sort(canonicalStringCompare);
  if (options.abortSignal) {
    return new Map(
      Object.entries(
        await _resolveSenioritySlugs(sorted, locale, options.abortSignal),
      ),
    );
  }
  const record = await _resolveSenioritySlugsCached(sorted, locale);
  return new Map(Object.entries(record));
}

async function _resolveSenioritySlugsCached(
  sortedSlugs: string[],
  locale: string,
): Promise<Record<string, TaxonomySuggestion>> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadSenioritiesCacheTag());

  return _resolveSenioritySlugs(sortedSlugs, locale);
}

async function _resolveSenioritySlugs(
  sortedSlugs: string[],
  locale: string,
  abortSignal?: AbortSignal,
): Promise<Record<string, TaxonomySuggestion>> {

  const rows = abortSignal
    ? await fetchSeniorityDocuments(locale, abortSignal)
    : await fetchSeniorityDocuments(locale);
  const wanted = new Set(sortedSlugs);
  const result: Record<string, TaxonomySuggestion> = {};
  for (const row of rows) {
    if (!wanted.has(row.slug)) continue;
    result[row.slug] = {
      id: row.seniority_id,
      slug: row.slug,
      name: row.name,
    };
  }
  return result;
}

/**
 * Expand an occupation ID to include all descendant (child) IDs.
 * If "Software Engineer" is selected, also match "Frontend Developer", "Backend Developer", etc.
 *
 * Per-region in-memory `'use cache'` (cacheLife('days')). Migrated from
 * Redis-backed `cached()` (TTL 86400s) in #2884 (resolve/expand slice,
 * bucket 3). Tagged so `crawler sync` -> `/api/internal/invalidate-typeahead`
 * drops the slot when the occupation hierarchy changes.
 *
 * Prefer {@link expandOccupationIdsBatch} when callers have multiple seed
 * IDs so one cached taxonomy snapshot resolves the union.
 */
export async function expandOccupationIds(occupationId: number): Promise<number[]> {
  return expandOccupationIdsBatch([occupationId]);
}

/**
 * Batch variant of {@link expandOccupationIds} — takes an array of seed
 * occupation IDs and walks the indexed `parent_id` metadata in memory to
 * return the deduplicated descendant union (issue #3186).
 *
 * Cache-key shape under `'use cache'`: the wrapper sorts the ID array
 * so `[a,b]` and `[b,a]` hit the same slot. Empty input short-circuits
 * before the cache boundary.
 */
export async function expandOccupationIdsBatch(
  occupationIds: number[],
): Promise<number[]> {
  if (occupationIds.length === 0) return [];
  const sorted = [...new Set(occupationIds)].sort((a, b) => a - b);
  return _expandOccupationIdsBatchCached(sorted);
}

async function _expandOccupationIdsBatchCached(
  sortedIds: number[],
): Promise<number[]> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadOccupationsCacheTag());

  const documents = await fetchOccupationDocuments("en");
  const descendants = new Set(sortedIds);
  let changed = true;
  while (changed) {
    changed = false;
    for (const document of documents) {
      if (
        document.parent_id != null &&
        descendants.has(document.parent_id) &&
        !descendants.has(document.occupation_id)
      ) {
        descendants.add(document.occupation_id);
        changed = true;
      }
    }
  }
  const known = new Set(documents.map((document) => document.occupation_id));
  return [...descendants].filter((id) => known.has(id)).sort((a, b) => a - b);
}

export async function resolveTechnologySlugs(
  slugs: string[],
  options: { abortSignal?: AbortSignal } = {},
): Promise<Map<string, TaxonomySuggestion>> {
  if (slugs.length === 0) return new Map();
  // See `resolveOccupationSlugs` for the canonicalization rationale.
  const sorted = [...slugs].sort(canonicalStringCompare);
  if (options.abortSignal) {
    return new Map(
      Object.entries(
        await _resolveTechnologySlugs(sorted, options.abortSignal),
      ),
    );
  }
  const record = await _resolveTechnologySlugsCached(sorted);
  return new Map(Object.entries(record));
}

async function _resolveTechnologySlugsCached(
  sortedSlugs: string[],
): Promise<Record<string, TaxonomySuggestion>> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadTechnologiesCacheTag());

  return _resolveTechnologySlugs(sortedSlugs);
}

async function _resolveTechnologySlugs(
  sortedSlugs: string[],
  abortSignal?: AbortSignal,
): Promise<Record<string, TaxonomySuggestion>> {

  const rows = abortSignal
    ? await fetchTechnologyDocuments(abortSignal)
    : await fetchTechnologyDocuments();
  const wanted = new Set(sortedSlugs);
  const result: Record<string, TaxonomySuggestion> = {};
  for (const row of rows) {
    if (!wanted.has(row.slug)) continue;
    result[row.slug] = {
      id: row.technology_id,
      slug: row.slug,
      name: row.name ?? row.slug,
    };
  }
  return result;
}

// ── All occupations grouped by domain (Typesense facets) ─────────────

export interface OccupationItem {
  id: number;
  slug: string;
  name: string;
  count: number;
  /**
   * Parent occupation id within the same domain. `null` for top-level
   * occupations (family parents and standalones). Plumbed through from
   * the underlying `OccupationMeta.parentId` so the modal can disable
   * descendant pills when a family parent or domain is selected.
   * See #2978.
   */
  parentId: number | null;
  /**
   * Domain id this occupation belongs to. `null` for occupations with no
   * domain (rare). Used to group occupation rows under the modal's domain
   * headers; domain headers toggle their disjoint first-level occupations
   * rather than entering the occupation-id filter namespace (#3027).
   */
  domainId: number | null;
}

/** A parent occupation with its children within a domain. */
export interface OccupationSubGroup {
  parent: OccupationItem;
  children: OccupationItem[];
}

export interface OccupationGroup {
  domain: { id: number; slug: string; name: string; count: number };
  /** Parent occupations with their children. */
  subGroups: OccupationSubGroup[];
  /** Occupations in this domain that have no parent and no children. */
  standalone: OccupationItem[];
}

export async function getAllOccupationsGrouped(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] },
  options: { failOnUnavailable?: boolean } = {},
): Promise<OccupationGroup[]> {
  // Stable cache key across array permutations — see #3187 and the helper
  // doc for the full rationale.
  const fKey = filters ? JSON.stringify(canonicalizeFilters(filters)) : "";
  // v2 cache-key bump (#3033 — facet switched from `occupation_id` to the
  // ancestor-expanded `occupation_ids`, so parent counts are now subtree
  // counts directly; sub-group/domain totals dropped the
  // sum-children-and-add-parent hack). Old v1 entries cached the buggy
  // summed counts.
  const key = `occ-all-grouped-v3:${locale}:${fKey}`;
  try {
    return await cached(key, () => _fetchAllOccupationsGrouped(locale, filters), {
      ttl: CACHE_TTL_LONG,
    });
  } catch (err) {
    if (options.failOnUnavailable || !isTypesenseUnavailableError(err)) {
      throw sanitizeTypesenseBoundaryError(err);
    }
    logExternalError(
      "error",
      { service: "typesense", operation: "all_occupations_grouped" },
      err,
    );
    return [];
  }
}

// ── Occupation hierarchy cache ───────────────────────────────────────

interface OccupationMeta {
  id: number;
  slug: string;
  parentId: number | null;
  domainId: number | null;
  names: Record<string, string>; // locale -> display name
}

interface OccupationDomainMeta {
  id: number;
  slug: string;
  names: Record<string, string>;
}

// Per-region in-memory `'use cache'` (cacheLife('days')). Build ID is
// included in the key automatically — every deploy re-fetches, which is
// the right TTL semantics for taxonomy data driven by `crawler sync`.
// Returns plain `Record`s (serializable); callers convert to `Map` for
// O(1) lookup ergonomics. Migrated from Redis-backed `cached()` in #2884
// (hierarchy-cache slice). See `apps/web/docs/cache-components.md`.
async function _fetchOccupationHierarchyData(): Promise<{
  occupations: Record<string, OccupationMeta>;
  domains: Record<string, OccupationDomainMeta>;
}> {
  "use cache";
  cacheLife("days");

  cacheTag(typeaheadOccupationsCacheTag());
  const occRows = await fetchOccupationDocuments("en");

  const occupations: Record<string, OccupationMeta> = {};
  const domains: Record<string, OccupationDomainMeta> = {};
  for (const row of occRows) {
    occupations[String(row.occupation_id)] = {
      id: row.occupation_id,
      slug: row.slug,
      parentId: row.parent_id ?? null,
      domainId: row.domain_id ?? null,
      names: { en: row.name },
    };
    if (row.domain_id != null && row.domain_slug) {
      domains[String(row.domain_id)] = {
        id: row.domain_id,
        slug: row.domain_slug,
        names: { en: row.domain_name ?? row.domain_slug },
      };
    }
  }

  return { occupations, domains };
}

async function _getOccupationHierarchyCache(): Promise<{
  occupations: Map<number, OccupationMeta>;
  domains: Map<number, OccupationDomainMeta>;
}> {
  const record = await _fetchOccupationHierarchyData();
  return {
    occupations: new Map(Object.entries(record.occupations).map(([k, v]) => [Number(k), v])),
    domains: new Map(Object.entries(record.domains).map(([k, v]) => [Number(k), v])),
  };
}

async function _getLocalizedOccupationHierarchyCache(locale: string): Promise<{
  occupations: Map<number, OccupationMeta>;
  domains: Map<number, OccupationDomainMeta>;
}> {
  if (locale === "en") return _getOccupationHierarchyCache();
  const rows = await fetchOccupationDocuments(locale);
  const occupations = new Map<number, OccupationMeta>();
  const domains = new Map<number, OccupationDomainMeta>();
  for (const row of rows) {
    occupations.set(row.occupation_id, {
      id: row.occupation_id,
      slug: row.slug,
      parentId: row.parent_id ?? null,
      domainId: row.domain_id ?? null,
      names: { [locale]: row.name },
    });
    if (row.domain_id != null && row.domain_slug) {
      domains.set(row.domain_id, {
        id: row.domain_id,
        slug: row.domain_slug,
        names: { [locale]: row.domain_name ?? row.domain_slug },
      });
    }
  }
  return { occupations, domains };
}

function _getLocaleName(names: Record<string, string>, locale: string, fallback: string): string {
  return names[locale] ?? names.en ?? fallback;
}

async function _fetchAllOccupationsGrouped(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<OccupationGroup[]> {
  const client = getTypesenseClient();
    const filterStr = buildFilterString(filters);

    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    // Facet on the ancestor-expanded `occupation_ids` (not `occupation_id`)
    // so parent / family-parent IDs receive the true subtree count directly
    // from the facet — the exporter stamps every posting with
    // `[self, parent_chain]`, so `occupation_ids` facet entries already
    // encode "match anywhere under this occupation node" semantics. See
    // exporter.py `_load_occupation_ancestors`. Issue #3033 — the previous
    // `occupation_id` facet returned only direct counts, and the modal
    // summed parent + children to fake a subtree total, which under-counts
    // whenever a posting is tagged at the parent tier (no child) or when a
    // mid-rank descendant falls below the top-N facet cutoff.
    const result = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`,
      facet_by: "occupation_ids",
      max_facet_values: 500,
      facet_strategy: "exhaustive",
      per_page: 0,
    });

    // Extract facet counts: ancestor_id -> count (occupation_ids contains
    // self + parent chain). For an occupation row, this is the true subtree
    // count. Domain ids deliberately never enter this namespace (#3027).
    const facetCounts = new Map<number, number>();
    const occFacet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === "occupation_ids",
    );
    if (occFacet) {
      for (const fc of (occFacet as { counts: Array<{ value: string; count: number }> }).counts) {
        facetCounts.set(Number(fc.value), fc.count);
      }
    }

    if (facetCounts.size === 0) return [];

    // Load hierarchy metadata
    const { occupations, domains } = await _getLocalizedOccupationHierarchyCache(locale);

    // Build items with counts from facet data. Every facet value is an
    // occupation id; occupation-domain ids remain a separate identifier
    // namespace and must not be interpreted as occupation ids (#3027).
    type OccRow = { id: number; slug: string; name: string; cnt: number; parentId: number | null; domainId: number | null };
    const items: OccRow[] = [];

    // Include occupations that have counts AND their parents (for sub-grouping)
    const idsWithCounts = new Set<number>();
    for (const occId of facetCounts.keys()) {
      if (occupations.has(occId)) idsWithCounts.add(occId);
    }
    const parentIdsNeeded = new Set<number>();

    for (const occId of idsWithCounts) {
      const meta = occupations.get(occId);
      if (!meta) continue;
      if (meta.parentId != null && !idsWithCounts.has(meta.parentId)) {
        parentIdsNeeded.add(meta.parentId);
      }
    }

    // Gather all relevant occupations. With `occupation_ids` facet, each
    // entry's count is already the subtree count (parent + descendants).
    for (const occId of idsWithCounts) {
      const meta = occupations.get(occId);
      if (!meta) continue;
      items.push({
        id: meta.id,
        slug: meta.slug,
        name: _getLocaleName(meta.names, locale, meta.slug),
        cnt: facetCounts.get(occId) ?? 0,
        parentId: meta.parentId,
        domainId: meta.domainId,
      });
    }

    // Add parent occupations that have children with counts but no direct
    // count themselves. With ancestor-expanded facet, a parent with any
    // matching descendant will normally already appear above — this loop
    // is a safety net for parents that fell below the top-N facet cutoff.
    for (const parentId of parentIdsNeeded) {
      const meta = occupations.get(parentId);
      if (!meta) continue;
      items.push({
        id: meta.id,
        slug: meta.slug,
        name: _getLocaleName(meta.names, locale, meta.slug),
        cnt: facetCounts.get(parentId) ?? 0,
        parentId: meta.parentId,
        domainId: meta.domainId,
      });
    }

    // Group by domain using the same logic as the original
    const domainRows = new Map<number, { meta: { id: number; slug: string; name: string }; rows: OccRow[] }>();
    const ungrouped: OccupationGroup[] = [];

    for (const r of items) {
      if (r.domainId != null) {
        const domainMeta = domains.get(r.domainId);
        if (domainMeta) {
          let bucket = domainRows.get(r.domainId);
          if (!bucket) {
            bucket = {
              meta: {
                id: domainMeta.id,
                slug: domainMeta.slug,
                name: _getLocaleName(domainMeta.names, locale, domainMeta.slug),
              },
              rows: [],
            };
            domainRows.set(r.domainId, bucket);
          }
          bucket.rows.push(r);
          continue;
        }
      }
      // No domain — standalone
      ungrouped.push({
        domain: { id: r.id, slug: r.slug, name: r.name, count: r.cnt },
        subGroups: [],
        standalone: [{
          id: r.id,
          slug: r.slug,
          name: r.name,
          count: r.cnt,
          parentId: r.parentId,
          domainId: r.domainId,
        }],
      });
    }

    // Build OccupationGroup per domain with parent-child sub-groups
    const groupedResult: OccupationGroup[] = [];

    for (const { meta, rows: domainItems } of domainRows.values()) {
      const idSet = new Set(domainItems.map((r) => r.id));
      const parentIds = new Set(
        domainItems
          .filter((r) => r.parentId != null && idSet.has(r.parentId))
          .map((r) => r.parentId!),
      );

      const subGroupMap = new Map<number, OccupationSubGroup>();
      const standalone: OccupationItem[] = [];

      // First pass: create sub-groups for parents
      for (const r of domainItems) {
        if (parentIds.has(r.id)) {
          subGroupMap.set(r.id, {
            parent: {
              id: r.id,
              slug: r.slug,
              name: r.name,
              count: r.cnt,
              parentId: r.parentId,
              domainId: r.domainId,
            },
            children: [],
          });
        }
      }

      // Second pass: assign children and standalone
      for (const r of domainItems) {
        if (r.parentId != null && subGroupMap.has(r.parentId)) {
          subGroupMap.get(r.parentId)!.children.push({
            id: r.id,
            slug: r.slug,
            name: r.name,
            count: r.cnt,
            parentId: r.parentId,
            domainId: r.domainId,
          });
        } else if (!parentIds.has(r.id)) {
          standalone.push({
            id: r.id,
            slug: r.slug,
            name: r.name,
            count: r.cnt,
            parentId: r.parentId,
            domainId: r.domainId,
          });
        }
      }

      // Sort sub-groups by parent subtree count (parent.count is already
      // the true subtree count under the `occupation_ids` facet, so we no
      // longer add children — that would double-count). Children within
      // a sub-group sort by their own subtree count desc.
      const subGroups = [...subGroupMap.values()].sort(
        (a, b) => b.parent.count - a.parent.count,
      );
      for (const sg of subGroups) {
        sg.children.sort((a, b) => b.count - a.count);
      }
      standalone.sort((a, b) => b.count - a.count);

      // Domain headers are UI groups, not occupation filters. Sum the
      // disjoint first-level occupation subtrees instead of looking up the
      // domain's numeric id in the occupation facet: the two tables use
      // independent sequences and their ids can collide (#3027).
      const firstLevelSum = subGroups.reduce((s, sg) => s + sg.parent.count, 0)
        + standalone.reduce((s, it) => s + it.count, 0);
      groupedResult.push({
        domain: { id: meta.id, slug: meta.slug, name: meta.name, count: firstLevelSum },
        subGroups,
        standalone,
      });
    }

    groupedResult.sort((a, b) => b.domain.count - a.domain.count);
    return [...groupedResult, ...ungrouped];
}

// ── All seniorities (Typesense facets) ──────────────────────────────

export interface SeniorityOption {
  id: number;
  slug: string;
  name: string;
  count: number;
}

export async function getAllSeniorities(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; technologyIds?: number[]; languages?: string[] },
  options: { failOnUnavailable?: boolean } = {},
): Promise<SeniorityOption[]> {
  // Stable cache key across array permutations — see #3187.
  const fKey = filters ? JSON.stringify(canonicalizeFilters(filters)) : "";
  const key = `sen-all-v2:${locale}:${fKey}`;
  try {
    return await cached(key, () => _fetchAllSeniorities(locale, filters), {
      ttl: CACHE_TTL_LONG,
    });
  } catch (err) {
    if (options.failOnUnavailable || !isTypesenseUnavailableError(err)) {
      throw sanitizeTypesenseBoundaryError(err);
    }
    logExternalError("error", { service: "typesense", operation: "all_seniorities" }, err);
    return [];
  }
}

// ── Seniority metadata cache ─────────────────────────────────────────

interface SeniorityMeta {
  id: number;
  slug: string;
  names: Record<string, string>;
}

// Per-region in-memory `'use cache'` (cacheLife('days')). See note on
// `_fetchOccupationHierarchyData` above. Migrated from Redis-backed
// `cached()` in #2884 (hierarchy-cache slice).
async function _fetchSeniorityHierarchyData(
  locale: string,
): Promise<Record<string, SeniorityMeta>> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadSenioritiesCacheTag());
  const rows = await fetchSeniorityDocuments(locale);

  const result: Record<string, SeniorityMeta> = {};
  for (const row of rows) {
    result[String(row.seniority_id)] = {
      id: row.seniority_id,
      slug: row.slug,
      names: { [locale]: row.name },
    };
  }
  return result;
}

async function _getSeniorityCache(locale: string): Promise<Map<number, SeniorityMeta>> {
  const record = await _fetchSeniorityHierarchyData(locale);
  return new Map(Object.entries(record).map(([k, v]) => [Number(k), v]));
}

async function _fetchAllSeniorities(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<SeniorityOption[]> {
  const client = getTypesenseClient();
    const filterStr = buildFilterString(filters);

    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    const result = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`,
      facet_by: "seniority_id",
      max_facet_values: 50,
      facet_strategy: "exhaustive",
      per_page: 0,
    });

    const senFacet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === "seniority_id",
    );
    if (!senFacet) return [];

    const senCache = await _getSeniorityCache(locale);

    const options: SeniorityOption[] = [];
    for (const fc of (senFacet as { counts: Array<{ value: string; count: number }> }).counts) {
      const senId = Number(fc.value);
      const meta = senCache.get(senId);
      if (!meta) continue;
      options.push({
        id: meta.id,
        slug: meta.slug,
        name: _getLocaleName(meta.names, locale, meta.slug),
        count: fc.count,
      });
    }

    // Sort by seniority id (preserve logical order)
    options.sort((a, b) => a.id - b.id);
    return options;
}

// ── All technologies grouped by category (Typesense facets) ──────────

export interface TechnologyItem {
  id: number;
  slug: string;
  name: string;
  count: number;
}

export interface TechnologyGroup {
  category: string;
  technologies: TechnologyItem[];
}

export async function getAllTechnologiesGrouped(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; languages?: string[] },
  options: { failOnUnavailable?: boolean } = {},
): Promise<TechnologyGroup[]> {
  // Stable cache key across array permutations — see #3187.
  const fKey = filters ? JSON.stringify(canonicalizeFilters(filters)) : "";
  const key = `tech-all-grouped-v2:${fKey}`;
  try {
    return await cached(key, () => _fetchAllTechnologiesGrouped(filters), {
      ttl: CACHE_TTL_LONG,
    });
  } catch (err) {
    if (options.failOnUnavailable || !isTypesenseUnavailableError(err)) {
      throw sanitizeTypesenseBoundaryError(err);
    }
    logExternalError(
      "error",
      { service: "typesense", operation: "all_technologies_grouped" },
      err,
    );
    return [];
  }
}

// ── Technology metadata cache ────────────────────────────────────────

interface TechnologyMeta {
  id: number;
  slug: string;
  name: string;
  category: string;
}

// Per-region in-memory `'use cache'` (cacheLife('days')). See note on
// `_fetchOccupationHierarchyData` above. Migrated from Redis-backed
// `cached()` in #2884 (hierarchy-cache slice — final slot, missed by
// bucket 1 because the issue body filed it under bucket 3). Tagged so
// `crawler sync` -> `/api/internal/invalidate-typeahead` evicts it
// alongside the matching tech typeahead slot.
async function _fetchTechnologyHierarchyData(): Promise<Record<string, TechnologyMeta>> {
  "use cache";
  cacheLife("days");
  // Tag the slot so `revalidateTag(typeaheadTechnologiesCacheTag())` from
  // /api/internal/invalidate-typeahead drops it after `crawler sync`,
  // matching the typeahead slot's invalidation.
  cacheTag(typeaheadTechnologiesCacheTag());

  const rows = await fetchTechnologyDocuments();

  const result: Record<string, TechnologyMeta> = {};
  for (const row of rows) {
    result[String(row.technology_id)] = {
      id: row.technology_id,
      slug: row.slug,
      name: row.name ?? row.slug,
      category: row.category ?? "other",
    };
  }
  return result;
}

async function _getTechnologyCache(): Promise<Map<number, TechnologyMeta>> {
  const record = await _fetchTechnologyHierarchyData();
  return new Map(Object.entries(record).map(([k, v]) => [Number(k), v]));
}

async function _fetchAllTechnologiesGrouped(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; languages?: string[] },
): Promise<TechnologyGroup[]> {
  const client = getTypesenseClient();
    const filterStr = buildFilterString(filters);

    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    const result = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`,
      facet_by: "technology_ids",
      max_facet_values: 500,
      facet_strategy: "exhaustive",
      per_page: 0,
    });

    const techFacet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === "technology_ids",
    );
    if (!techFacet) return [];

    const techCache = await _getTechnologyCache();

    // Group by category
    const groups = new Map<string, TechnologyItem[]>();
    for (const fc of (techFacet as { counts: Array<{ value: string; count: number }> }).counts) {
      const techId = Number(fc.value);
      const meta = techCache.get(techId);
      if (!meta) continue;
      const items = groups.get(meta.category) ?? [];
      items.push({
        id: meta.id,
        slug: meta.slug,
        name: meta.name,
        count: fc.count,
      });
      groups.set(meta.category, items);
    }

    // Sort each category's items by count desc, then sort groups by total count desc
    return [...groups.entries()]
      .map(([category, technologies]) => {
        technologies.sort((a, b) => b.count - a.count);
        return { category, technologies };
      })
      .sort((a, b) => {
        const aTotal = a.technologies.reduce((s, t) => s + t.count, 0);
        const bTotal = b.technologies.reduce((s, t) => s + t.count, 0);
        return bTotal - aTotal;
      });
}

// ── Facet-count helpers for fixed-option modals ──────────────────────
//
// `employment_type` and `location_types` (work-mode) are small fixed
// enums; the modals own the option list and i18n labels (see
// `employment-type-modal.tsx` and `work-mode-modal.tsx`). The web app
// only needs per-option counts to display next to each label — same
// UX parity as the seniority/technology modals. Issue #3032.
//
// Both helpers re-use the shared `buildFilterString` cross-filter
// pipeline, so counts reflect the currently-applied filter context
// (location, occupation, level, etc.) just like the other modals.
// Missing keys in the returned record mean "0 matching postings" —
// the modals render `(0)` for them.

/** Map of employment_type value -> matching active-posting count. */
export async function getEmploymentTypeCounts(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; workMode?: string[]; languages?: string[] },
): Promise<Record<string, number>> {
  const fKey = filters ? JSON.stringify(canonicalizeFilters(filters)) : "";
  const key = `emp-type-counts:${fKey}`;
  return cached(key, () => _fetchEmploymentTypeCounts(filters), { ttl: CACHE_TTL_LONG });
}

async function _fetchEmploymentTypeCounts(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; workMode?: string[]; languages?: string[] },
): Promise<Record<string, number>> {
  try {
    const client = getTypesenseClient();
    const filterStr = buildFilterString(filters);

    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    const result = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`,
      facet_by: "employment_type",
      max_facet_values: 50,
      facet_strategy: "exhaustive",
      per_page: 0,
    });

    const facet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === "employment_type",
    );
    if (!facet) return {};
    const out: Record<string, number> = {};
    for (const fc of (facet as { counts: Array<{ value: string; count: number }> }).counts) {
      out[fc.value] = fc.count;
    }
    return out;
  } catch {
    return {};
  }
}

/** Map of work-mode value (`onsite`|`hybrid`|`remote`) -> matching active-posting count. */
export async function getWorkModeCounts(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; employmentTypes?: string[]; languages?: string[] },
): Promise<Record<string, number>> {
  const fKey = filters ? JSON.stringify(canonicalizeFilters(filters)) : "";
  const key = `work-mode-counts:${fKey}`;
  return cached(key, () => _fetchWorkModeCounts(filters), { ttl: CACHE_TTL_LONG });
}

async function _fetchWorkModeCounts(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; employmentTypes?: string[]; languages?: string[] },
): Promise<Record<string, number>> {
  try {
    const client = getTypesenseClient();
    const filterStr = buildFilterString(filters);

    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    const result = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`,
      facet_by: "location_types",
      max_facet_values: 50,
      facet_strategy: "exhaustive",
      per_page: 0,
    });

    const facet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === "location_types",
    );
    if (!facet) return {};
    const out: Record<string, number> = {};
    for (const fc of (facet as { counts: Array<{ value: string; count: number }> }).counts) {
      out[fc.value] = fc.count;
    }
    return out;
  } catch {
    return {};
  }
}
