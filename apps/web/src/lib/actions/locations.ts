"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";

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
}): Promise<LocationSuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];

  const key = `loc-suggest:${q}:${params.locale}:${params.userLat ?? ""}:${params.userLng ?? ""}`;
  return cached(key, () => _querySuggestions(params), { ttl: 3600 });
}

async function _querySuggestions(params: {
  query: string;
  locale: string;
  userLat?: number;
  userLng?: number;
}): Promise<LocationSuggestion[]> {
  const q = params.query.trim().toLowerCase();
  const { locale, userLat, userLng } = params;
  const hasGeo = userLat != null && userLng != null;

  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    name: string;
    type: string;
    parent_name: string | null;
    population: number;
    lat: number | null;
    lng: number | null;
    match_rank: number;
  }>(sql`
    WITH active_locs AS (
      WITH RECURSIVE job_locs AS (
        SELECT DISTINCT unnest(location_ids) AS id
        FROM job_posting WHERE is_active = true
      ),
      ancestors AS (
        SELECT id FROM job_locs
        UNION
        SELECT l.parent_id FROM ancestors a
        JOIN location l ON l.id = a.id WHERE l.parent_id IS NOT NULL
      )
      SELECT id FROM ancestors WHERE id IS NOT NULL
      UNION
      SELECT lm.macro_id FROM ancestors a
      JOIN location_macro_member lm ON lm.country_id = a.id
    ),
    prefix_matches AS (
      SELECT DISTINCT ON (l.id) l.id, l.type, l.population, l.lat, l.lng, l.parent_id,
             1 AS match_rank
      FROM location_name ln
      JOIN location l ON l.id = ln.location_id
      JOIN active_locs al ON al.id = l.id
      WHERE ln.locale = ${locale}
        AND lower(ln.name) LIKE ${q + "%"}
      ORDER BY l.id
    ),
    fuzzy_matches AS (
      SELECT DISTINCT ON (l.id) l.id, l.type, l.population, l.lat, l.lng, l.parent_id,
             2 AS match_rank
      FROM location_name ln
      JOIN location l ON l.id = ln.location_id
      JOIN active_locs al ON al.id = l.id
      WHERE ln.locale = ${locale}
        AND length(${q}) >= 3
        AND similarity(lower(ln.name), ${q}) > 0.25
        AND l.id NOT IN (SELECT id FROM prefix_matches)
      ORDER BY l.id, similarity(lower(ln.name), ${q}) DESC
    ),
    matches AS (
      SELECT * FROM prefix_matches
      UNION ALL
      SELECT * FROM fuzzy_matches
    )
    SELECT m.id,
      loc.slug,
      dn.name,
      m.type::text AS type,
      pdn.name AS parent_name,
      COALESCE(m.population, 0) AS population,
      m.lat, m.lng,
      m.match_rank
    FROM matches m
    JOIN location loc ON loc.id = m.id
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = m.id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) dn ON true
    LEFT JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = m.parent_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) pdn ON true
  `);

  type Row = { id: number; slug: string; name: string; type: string; parent_name: string | null; population: number; lat: number | null; lng: number | null; match_rank: number };
  const all = rows as unknown as Row[];

  // Sort: nearby locations by distance, then far locations by population
  const NEAR_KM = 300;
  const sorted = all
    .map((r) => ({
      ...r,
      dist: hasGeo && r.lat != null && r.lng != null
        ? _haversineKm(userLat!, userLng!, r.lat, r.lng)
        : Infinity,
    }))
    .sort((a, b) => {
      if (a.match_rank !== b.match_rank) return a.match_rank - b.match_rank;
      const nearA = a.dist < NEAR_KM;
      const nearB = b.dist < NEAR_KM;
      if (nearA && nearB) return a.dist - b.dist;
      if (nearA !== nearB) return nearA ? -1 : 1;
      return b.population - a.population;
    })
    .slice(0, 8);

  return sorted.map((r) => ({
    id: r.id,
    slug: r.slug,
    name: r.name,
    type: r.type as LocationSuggestion["type"],
    parentName: r.parent_name,
  }));
}

/**
 * Expand a location ID to include all descendant IDs.
 * Used by search to match "Switzerland" → all jobs in Swiss cities.
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

function _haversineKm(
  lat1: number, lng1: number,
  lat2: number, lng2: number,
): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
