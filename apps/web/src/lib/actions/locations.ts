"use server";

import { sql } from "drizzle-orm";
import { cacheLife } from "next/cache";
import { db } from "@/db";
import { cached } from "@/lib/cache";
import { getTypesenseClient, type TypesenseHit } from "@/lib/search/typesense-client";
import { buildFilterString } from "@/lib/search/typesense-filters";
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
 */
export async function expandLocationIds(locationId: number): Promise<number[]> {
  const key = `loc-expand:${locationId}`;
  return cached(
    key,
    async () => {
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
    },
    { ttl: 86400 },
  );
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
  const key = `loc-resolve-slugs:${slugs.sort().join(",")}:${locale}`;
  // Cache as a plain record (Map doesn't survive JSON serialization in Redis)
  const record = await cached(
    key,
    async () => {
      const pgArray = `{${slugs.join(",")}}`;
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
    },
    { ttl: 3600 },
  );
  return new Map(Object.entries(record));
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

export async function getGlobalLocationsGrouped(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; occupationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<GlobalLocationGroup[]> {
  const fKey = filters ? JSON.stringify(filters) : "";
  const key = `global-locs-grouped:${locale}:${fKey}`;
  return cached(key, () => _fetchGlobalLocationsGrouped(locale, filters), { ttl: 3600 });
}

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
): Promise<GlobalLocationGroup[]> {
  try {
    const client = getTypesenseClient();

    // Build filter string for the facet query (excludes location filter itself)
    const filterStr = buildFilterString(filters);

    // Build the query string for keyword matching
    const hasKeywords = filters?.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters!.keywords!.join(" ") : "*";

    const result = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: `is_active:true${filterStr ? " && " + filterStr : ""}`,
      facet_by: "location_ids",
      max_facet_values: 500,
      facet_strategy: "exhaustive",
      per_page: 0,
    });

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

    if (facetCounts.size === 0) return [];

    // Load hierarchy metadata
    const hierarchy = await _getLocationHierarchyCache();

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

    return [...countries.values()]
      .filter((g) => g.regions.some((r) => r.locations.length > 0))
      .sort((a, b) => {
        // Sort by country name alphabetically
        return a.countryName.localeCompare(b.countryName);
      });
  } catch {
    // Typesense unavailable — return empty
    return [];
  }
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
