"use server";

import { sql } from "drizzle-orm";
import { cacheLife, cacheTag } from "next/cache";
import { db } from "@/db";
import { cached } from "@/lib/cache";
import { typeaheadLocationsCacheTag } from "@/lib/cache-tags";
import { getTypesenseClient, type TypesenseHit } from "@/lib/search/typesense-client";
import { buildFilterString, POSTING_BASE_FILTER } from "@/lib/search/typesense-filters";
import { boostByFilterMatches, type TypeaheadBoostFilters } from "@/lib/search/typeahead-boost";

export interface LocationSuggestion {
  id: number;
  slug: string;
  name: string;
  type: "macro" | "country" | "region" | "city";
  parentName: string | null;
}

export async function suggestLocations(params: {
  query: string;
  locale: string;
  userLat?: number;
  userLng?: number;
  filters?: TypeaheadBoostFilters;
}): Promise<LocationSuggestion[]> {
  const q = params.query.trim();
  if (q.length < 2) return [];

  // Bucket geo to 1-decimal precision (~10km) so callers in the same city
  // share a cache slot. Typesense ranks by `coordinates(lat,lng, precision:
  // 5km)` so this granularity keeps results indistinguishable in practice
  // while delivering a usable hit rate. See issue #2641.
  // The bucketed lat/lng are passed to `_fetchLocationSuggestionsCached`
  // as the cache-key inputs (raw lat/lng would shred the hit rate).
  const bucketedLat =
    params.userLat != null ? Number(params.userLat.toFixed(1)) : null;
  const bucketedLng =
    params.userLng != null ? Number(params.userLng.toFixed(1)) : null;

  // Per-region in-memory `'use cache'` (revalidate 3600s). Build ID is
  // included in the key automatically. Migrated from Redis-backed
  // `cached()` in #2884 (typeaheads slice). The previous `skipIf: r ===
  // null` semantics aren't available under `'use cache'`, so the inner
  // fetcher throws on Typesense unavailability and the wrapper catches
  // and returns `[]` — preventing outage-shaped empties from being pinned.
  // See `apps/web/docs/cache-components.md`.
  let suggestions: LocationSuggestion[];
  try {
    suggestions = await _fetchLocationSuggestionsCached(
      q.toLowerCase(),
      params.locale,
      bucketedLat,
      bucketedLng,
    );
  } catch {
    suggestions = [];
  }

  // Boost is per-call (depends on the user's currently-selected filters),
  // so it must run *after* the cached layer. Boosting is a pure re-sort
  // of the suggestion list and does no I/O.
  if (!params.filters) return suggestions;
  return boostByFilterMatches(
    suggestions,
    "location_ids",
    (s) => s.id,
    params.filters,
  );
}

/**
 * Cached inner fetch + mapping for {@link suggestLocations}. Throws if
 * Typesense is unreachable so the wrapper can swallow the error and avoid
 * pinning an outage-shaped empty list inside the `'use cache'` boundary.
 * Empty array is a legitimate "no match" result and IS cached.
 *
 * The function takes scalar args (not the params object) so the implicit
 * `'use cache'` argument-hash key reflects exactly the inputs that affect
 * the result. `bucketedLat`/`bucketedLng` are pre-rounded to 1-decimal
 * by the caller for cross-user hit-rate (see issue #2641).
 */
async function _fetchLocationSuggestionsCached(
  q: string,
  locale: string,
  bucketedLat: number | null,
  bucketedLng: number | null,
): Promise<LocationSuggestion[]> {
  "use cache";
  cacheLife({ revalidate: 3600 });
  // Tag the slot so `revalidateTag(typeaheadLocationsCacheTag())` from
  // /api/internal/invalidate-typeahead drops it after `crawler sync`,
  // instead of waiting up to 3600s for the TTL. See #2907 follow-up.
  cacheTag(typeaheadLocationsCacheTag());

  const hasGeo = bucketedLat != null && bucketedLng != null;
  const sortBy = hasGeo
    ? `_text_match:desc,coordinates(${bucketedLat},${bucketedLng}, precision: 5km):asc,active_posting_count:desc`
    : "_text_match:desc,active_posting_count:desc";

  const queryByFields = locale !== "en" ? `name_${locale},name_en` : "name_en";
  const queryByWeights = locale !== "en" ? "3,1" : "1";

  let result;
  try {
    const client = getTypesenseClient();
    result = await client.collections("location").documents().search({
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
  } catch (err) {
    // Throw past the cache boundary so the wrapper can return `[]` without
    // poisoning the cache slot for the next 3600s.
    throw err instanceof Error ? err : new Error(String(err));
  }

  if (!result.hits || result.hits.length === 0) return [];
  return result.hits.map((hit) =>
    _mapLocationHit(hit as unknown as TypesenseHit, locale),
  );
}

function _mapLocationHit(hit: TypesenseHit, locale: string): LocationSuggestion {
  const doc = hit.document;
  return {
    id: doc.location_id as number,
    slug: doc.slug as string,
    name: (doc[`name_${locale}`] ?? doc.name_en) as string,
    type: doc.type as LocationSuggestion["type"],
    parentName: (doc.parent_name as string) ?? null,
  };
}

/**
 * Expand a location ID to include all descendant IDs.
 * Used by search to match "Switzerland" -> all jobs in Swiss cities.
 *
 * Per-region in-memory `'use cache'` (cacheLife('days')). Migrated from
 * Redis-backed `cached()` (TTL 86400s) in #2884 (resolve/expand slice,
 * bucket 3). Tagged so `crawler sync` ->
 * `/api/internal/invalidate-typeahead` drops the slot when the location
 * hierarchy changes (macro region members in particular).
 */
export async function expandLocationIds(locationId: number): Promise<number[]> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadLocationsCacheTag());

  const rows = await db.execute<{ [key: string]: unknown; id: number }>(sql`
    WITH RECURSIVE seeds AS (
      -- The location itself
      SELECT id FROM location WHERE id = ${locationId}
      UNION
      -- If it's a macro region, include its member countries
      SELECT lm.country_id AS id
      FROM location_macro_member lm
      WHERE lm.macro_id = ${locationId}
    ),
    descendants AS (
      SELECT id FROM seeds
      UNION ALL
      SELECT l.id FROM location l JOIN descendants d ON l.parent_id = d.id
    )
    SELECT id FROM descendants
  `);
  return (rows as unknown as { id: number }[]).map((r) => r.id);
}

export interface ResolvedLocation {
  id: number;
  slug: string;
  name: string;
  type: string;
  parentName: string | null;
}

export async function resolveLocationSlugs(
  slugs: string[],
  locale: string,
): Promise<Map<string, ResolvedLocation>> {
  if (slugs.length === 0) return new Map();
  const sorted = [...slugs].sort();
  // Plain `Record` survives the `'use cache'` boundary; Map is not
  // serializable, so the wrapper converts at the edge for caller ergonomics.
  const record = await _resolveLocationSlugsCached(sorted, locale);
  return new Map(Object.entries(record));
}

/**
 * Per-region in-memory `'use cache'` (cacheLife('days')). Migrated from
 * Redis-backed `cached()` (TTL 3600s) in #2884 (resolve/expand slice,
 * bucket 3). The wrapper sorts the slug array so `[a,b]` and `[b,a]` hit
 * the same `'use cache'` slot. Tagged so `crawler sync` ->
 * `/api/internal/invalidate-typeahead` drops the slot when location names
 * are renamed.
 */
async function _resolveLocationSlugsCached(
  sortedSlugs: string[],
  locale: string,
): Promise<Record<string, ResolvedLocation>> {
  "use cache";
  cacheLife("days");
  cacheTag(typeaheadLocationsCacheTag());

  const pgArray = `{${sortedSlugs.join(",")}}`;
  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    type: string;
    name: string;
    parent_name: string | null;
  }>(sql`
    SELECT l.id, l.slug, l.type::text AS type,
      ln.name,
      pln.name AS parent_name
    FROM location l
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = l.id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) ln ON true
    LEFT JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = l.parent_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) pln ON true
    WHERE l.slug = ANY(${pgArray}::text[])
  `);
  const result: Record<string, ResolvedLocation> = {};
  for (const r of rows as unknown as { id: number; slug: string; type: string; name: string; parent_name: string | null }[]) {
    result[r.slug] = {
      id: r.id,
      slug: r.slug,
      name: r.name,
      type: r.type,
      parentName: r.parent_name,
    };
  }
  return result;
}

// ── All locations grouped by country / region (global) ───────────────

export interface GlobalLocationGroup {
  countryId: number;
  countrySlug: string;
  countryName: string;
  countryCount: number;
  regions: {
    regionId: number;
    regionSlug: string;
    regionName: string;
    regionCount: number;
    locations: { id: number; slug: string; name: string; type: string; count: number }[];
  }[];
}

export interface GlobalMacroRegion {
  id: number;
  slug: string;
  /**
   * Canonical display name (e.g. "European Union" rather than "EU"). Falls
   * back to the localized name from `location_name` when no canonical
   * mapping exists for the slug. This is the value used both as the chip
   * label inside the Regions cluster AND as the `SelectedLocation.name`
   * carried into `FilterBar`/`SearchBar`, so changing it here updates both
   * the modal and the rendered filter pill consistently. See issue #2940
   * test plan: clicking EU yields a filter chip displaying "European Union".
   */
  name: string;
  /**
   * The localized abbreviation as stored in `location_name` (e.g. "EU",
   * "DACH"). Used by the modal-internal text-search filter so users can
   * match either the canonical name OR the abbreviation. Once #2939's
   * `aliases[]` field lands on the Typesense `location` collection, this
   * can grow into a richer alias array.
   */
  abbreviation: string;
  count: number;
  /** Member country names (English) — for the chip's hover tooltip. */
  memberCountryNames: string[];
}

export interface GlobalLocationsResponse {
  /**
   * Macro regions (EU, EMEA, DACH, …) with at least one active posting.
   * Sorted by count desc. Empty when Typesense is unreachable or no
   * macros have postings.
   */
  macros: GlobalMacroRegion[];
  /**
   * Country-rooted hierarchy. Existing shape — preserved unchanged so the
   * rest of the modal continues to render countries → regions → cities
   * exactly as before.
   */
  countries: GlobalLocationGroup[];
}

export async function getGlobalLocationsGrouped(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<GlobalLocationsResponse> {
  const fKey = filters ? JSON.stringify(filters) : "";
  // v3 cache-key bump (#2940 — added Regions cluster + dedicated macro
  // facet query). Old v1/v2 entries cached the array shape and would
  // otherwise be deserialized into the new wrapper object via the
  // run-time JSON path, then fail to render the macro tier.
  const key = `global-locs-grouped-v3:${locale}:${fKey}`;
  return cached(key, () => _fetchGlobalLocationsGrouped(locale, filters), { ttl: 3600 });
}

/**
 * Canonical display labels for macro regions. Stored DB names are short
 * abbreviations ("EU", "DACH", "EMEA") which read as alphabet-soup in chip
 * UI; this map expands the most-common ones to their full names. The slug
 * is used as the lookup key — falls back to the localized DB name when the
 * slug isn't in the map (e.g. NULL slug or a future addition).
 *
 * In the en/de/fr/it fall-through case we still pass the abbreviation
 * through so non-English speakers see the same text as on the search bar
 * dropdown — this matches the "consistent label" criterion in #2940's
 * test plan.
 */
const MACRO_DISPLAY_NAMES: Record<string, string> = {
  eu: "European Union",
  emea: "Europe, Middle East & Africa",
  dach: "DACH (Germany, Austria, Switzerland)",
  apac: "Asia-Pacific",
  americas: "Americas",
  latam: "Latin America",
  nordics: "Nordics",
  mena: "Middle East & North Africa",
  worldwide: "Worldwide",
};

// ── Location hierarchy cache (from Supabase Postgres, long TTL) ──────

interface LocationMeta {
  id: number;
  slug: string;
  type: string;
  parentId: number | null;
  names: Record<string, string>; // locale -> display name
}

// Per-region in-memory `'use cache'` (cacheLife('days')). Build ID is
// included in the key automatically — every deploy re-fetches, which is
// the right TTL semantics for taxonomy data driven by `crawler sync`.
// Returns a plain `Record` (serializable); the wrapper converts to `Map`
// for O(1) lookup ergonomics. Migrated from Redis-backed `cached()` in
// #2884 (hierarchy-cache slice). See `apps/web/docs/cache-components.md`.
async function _fetchLocationHierarchyData(): Promise<Record<string, LocationMeta>> {
  "use cache";
  cacheLife("days");

  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    type: string;
    parent_id: number | null;
  }>(sql`SELECT id, slug, type::text AS type, parent_id FROM location`);

  const nameRows = await db.execute<{
    [key: string]: unknown;
    location_id: number;
    locale: string;
    name: string;
  }>(sql`SELECT location_id, locale, name FROM location_name WHERE is_display = true`);

  const nameMap = new Map<number, Record<string, string>>();
  for (const nr of nameRows as unknown as { location_id: number; locale: string; name: string }[]) {
    let names = nameMap.get(nr.location_id);
    if (!names) { names = {}; nameMap.set(nr.location_id, names); }
    names[nr.locale] = nr.name;
  }

  const result: Record<string, LocationMeta> = {};
  for (const r of rows as unknown as { id: number; slug: string; type: string; parent_id: number | null }[]) {
    result[String(r.id)] = {
      id: r.id,
      slug: r.slug,
      type: r.type,
      parentId: r.parent_id,
      names: nameMap.get(r.id) ?? {},
    };
  }
  return result;
}

async function _getLocationHierarchyCache(): Promise<Map<number, LocationMeta>> {
  const record = await _fetchLocationHierarchyData();
  return new Map(Object.entries(record).map(([k, v]) => [Number(k), v]));
}

function _getLocaleName(meta: LocationMeta, locale: string): string {
  return meta.names[locale] ?? meta.names.en ?? meta.slug;
}

async function _fetchGlobalLocationsGrouped(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<GlobalLocationsResponse> {
  try {
    const client = getTypesenseClient();

    // Build filter string for the facet query (excludes location filter itself)
    const filterStr = buildFilterString(filters);

    // Build the query string for keyword matching
    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    // Load hierarchy metadata up-front so we know which IDs are macros
    // (used both for the dedicated macro facet query below AND for the
    // country-tier hierarchy walk further down).
    const hierarchy = await _getLocationHierarchyCache();
    const allMacroIds: number[] = [];
    for (const meta of hierarchy.values()) {
      if (meta.type === "macro") allMacroIds.push(meta.id);
    }

    // Run the country-tier facet query AND a dedicated macro-only facet
    // query in parallel. Reason for separating them: with `max_facet_values:
    // 500`, the country-tier facet truncates after the top-500 location
    // IDs by count. Macros (which are aggregated via ancestor expansion)
    // can have low counts (e.g. DACH=6) and fall below this cutoff, so we
    // re-query with `filter_by: location_ids:[<macroIds>]` to force the
    // facet to surface every macro with at least one matching posting.
    // This is much cheaper than raising the global `max_facet_values` —
    // there are only 9 macros today so the second query returns at most
    // 9 facet entries.
    const macroFilterClause = allMacroIds.length > 0
      ? `location_ids:[${allMacroIds.join(",")}]`
      : null;

    const baseSearchParams = {
      q,
      query_by: "title",
      filter_by: `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`,
      facet_by: "location_ids",
      max_facet_values: 500,
      facet_strategy: "exhaustive" as const,
      per_page: 0,
    };

    const [result, macroResult] = await Promise.all([
      client.collections("job_posting").documents().search(baseSearchParams),
      macroFilterClause
        ? client.collections("job_posting").documents().search({
            ...baseSearchParams,
            filter_by: `${baseSearchParams.filter_by} && ${macroFilterClause}`,
          })
        : Promise.resolve(null),
    ]);

    // Extract facet counts: location_id -> count
    const facetCounts = new Map<number, number>();
    const locationFacet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === "location_ids",
    );
    if (locationFacet) {
      for (const fc of (locationFacet as { counts: Array<{ value: string; count: number }> }).counts) {
        facetCounts.set(Number(fc.value), fc.count);
      }
    }

    // Macro counts: from the dedicated macro-filtered query (always reliable)
    const macroFacetCounts = new Map<number, number>();
    if (macroResult) {
      const macroFacet = (macroResult as { facet_counts?: Array<{ field_name: string; counts: Array<{ value: string; count: number }> }> })
        .facet_counts?.find((f) => f.field_name === "location_ids");
      if (macroFacet) {
        for (const fc of macroFacet.counts) {
          macroFacetCounts.set(Number(fc.value), fc.count);
        }
      }
    }

    if (facetCounts.size === 0 && macroFacetCounts.size === 0) {
      return { macros: [], countries: [] };
    }

    // Build macro-region cluster from the dedicated macro-only facet
    // result (NOT the truncated top-500 country-tier facet). Ancestor
    // expansion in `exporter.py` already promotes macro IDs onto each
    // posting's `location_ids`, so the facet count for a macro reflects
    // every posting whose country (transitively) belongs to it. See
    // `_fetchGlobalMacroMembers` for the per-macro member country names
    // used as the chip's hover tooltip.
    const macroIdsWithCounts = allMacroIds.filter((id) => (macroFacetCounts.get(id) ?? 0) > 0);
    const macroMemberNames = macroIdsWithCounts.length > 0
      ? await _fetchGlobalMacroMembers(macroIdsWithCounts, locale)
      : new Map<number, string[]>();
    const macros: GlobalMacroRegion[] = macroIdsWithCounts
      .map((id) => {
        const meta = hierarchy.get(id);
        if (!meta) return null;
        const abbreviation = _getLocaleName(meta, locale);
        const slugKey = (meta.slug ?? "").toLowerCase()
          || abbreviation.toLowerCase().replace(/\s+/g, "-");
        const canonical = MACRO_DISPLAY_NAMES[slugKey];
        return {
          id,
          slug: meta.slug ?? slugKey,
          name: canonical ?? abbreviation,
          abbreviation,
          count: macroFacetCounts.get(id) ?? 0,
          memberCountryNames: macroMemberNames.get(id) ?? [],
        } satisfies GlobalMacroRegion;
      })
      .filter((m): m is GlobalMacroRegion => m !== null && m.count > 0)
      .sort((a, b) => b.count - a.count);

    // Build country -> region -> city structure from flat facet results
    const countries = new Map<number, GlobalLocationGroup>();
    const directCountryCount = new Map<number, number>();
    const directRegionCount = new Map<number, number>();

    for (const [locationId, count] of facetCounts) {
      const loc = hierarchy.get(locationId);
      if (!loc) continue;

      // Find region and country for this location
      let regionId: number | null = null;
      let countryId: number | null = null;

      if (loc.type === "country") {
        countryId = loc.id;
        directCountryCount.set(countryId, count);
        // Ensure country group exists
        _ensureCountryGroup(countries, countryId, loc, locale);
        continue;
      }

      if (loc.type === "region") {
        regionId = loc.id;
        // parent should be country
        const parent = loc.parentId ? hierarchy.get(loc.parentId) : null;
        countryId = parent?.type === "country" ? parent.id : null;
        directRegionCount.set(regionId, count);
        // Ensure country group exists
        if (countryId) _ensureCountryGroup(countries, countryId, parent!, locale);
        continue;
      }

      if (loc.type === "city") {
        const parent = loc.parentId ? hierarchy.get(loc.parentId) : null;
        if (parent?.type === "region") {
          regionId = parent.id;
          const grandparent = parent.parentId ? hierarchy.get(parent.parentId) : null;
          countryId = grandparent?.type === "country" ? grandparent.id : null;
        } else if (parent?.type === "country") {
          countryId = parent.id;
        }
      }

      const cid = countryId ?? 0;
      const countryMeta = countryId ? hierarchy.get(countryId) : null;
      let country = countries.get(cid);
      if (!country) {
        country = {
          countryId: cid,
          countrySlug: countryMeta?.slug ?? "",
          countryName: countryMeta ? _getLocaleName(countryMeta, locale) : "Other",
          countryCount: 0,
          regions: [],
        };
        countries.set(cid, country);
      }

      const rid = regionId ?? 0;
      let region = country.regions.find((rg) => rg.regionId === rid);
      if (!region) {
        const regionMeta = regionId ? hierarchy.get(regionId) : null;
        region = {
          regionId: rid,
          regionSlug: regionMeta?.slug ?? "",
          regionName: regionMeta ? _getLocaleName(regionMeta, locale) : "",
          regionCount: 0,
          locations: [],
        };
        country.regions.push(region);
      }

      region.locations.push({
        id: locationId,
        slug: loc.slug,
        name: _getLocaleName(loc, locale),
        type: loc.type,
        count,
      });
    }

    // Aggregate counts bottom-up.
    // With ancestor IDs stored on documents, the facet already returns correct
    // counts for regions/countries (they include all descendant postings).
    // We only roll up from cities — do NOT add directRegionCount/directCountryCount
    // to avoid double-counting.
    for (const country of countries.values()) {
      let countryTotal = 0;
      for (const region of country.regions) {
        const cityTotal = region.locations.reduce((sum, l) => sum + l.count, 0);
        region.regionCount = cityTotal;
        countryTotal += region.regionCount;
        // Sort locations within region by count desc
        region.locations.sort((a, b) => b.count - a.count);
      }
      country.countryCount = countryTotal;
      // Sort regions by count desc
      country.regions.sort((a, b) => b.regionCount - a.regionCount);
    }

    const sortedCountries = [...countries.values()]
      .filter((g) => g.regions.some((r) => r.locations.length > 0))
      .sort((a, b) => {
        // Sort by country name alphabetically
        return a.countryName.localeCompare(b.countryName);
      });

    return { macros, countries: sortedCountries };
  } catch {
    // Typesense unavailable — return empty
    return { macros: [], countries: [] };
  }
}

/**
 * For each macro region, fetch the names of its member countries (in the
 * caller's locale, falling back to English). Used to populate the chip's
 * hover tooltip in {@link LocationSearchModal}.
 *
 * NOTE: in production today `location_macro_member` may be sparsely
 * populated — macros are still useful (ancestor expansion in
 * `exporter.py` promotes the macro ID onto each posting via the
 * `country_id -> [macro_ids]` map, even when that map is empty in the
 * particular DB snapshot we read from). When the table is empty we return
 * an empty member list and the modal renders the chip without a tooltip.
 */
async function _fetchGlobalMacroMembers(
  macroIds: number[],
  locale: string,
): Promise<Map<number, string[]>> {
  if (macroIds.length === 0) return new Map();
  const pgArray = `{${macroIds.join(",")}}`;
  const rows = await db.execute<{
    [key: string]: unknown;
    macro_id: number;
    country_name: string;
  }>(sql`
    SELECT lmm.macro_id, ln.name AS country_name
    FROM location_macro_member lmm
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = lmm.country_id
        AND locale IN (${locale}, 'en')
        AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) ln ON true
    WHERE lmm.macro_id = ANY(${pgArray}::integer[])
    ORDER BY lmm.macro_id, ln.name
  `);
  const map = new Map<number, string[]>();
  for (const r of rows as unknown as { macro_id: number; country_name: string }[]) {
    let arr = map.get(r.macro_id);
    if (!arr) { arr = []; map.set(r.macro_id, arr); }
    arr.push(r.country_name);
  }
  return map;
}

function _ensureCountryGroup(
  countries: Map<number, GlobalLocationGroup>,
  countryId: number,
  meta: LocationMeta,
  locale: string,
): void {
  if (!countries.has(countryId)) {
    countries.set(countryId, {
      countryId,
      countrySlug: meta.slug,
      countryName: _getLocaleName(meta, locale),
      countryCount: 0,
      regions: [],
    });
  }
}
