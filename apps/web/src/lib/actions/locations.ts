"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";

export interface LocationSuggestion {
  id: number;
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
    name: string;
    type: string;
    parent_name: string | null;
    population: number;
    lat: number | null;
    lng: number | null;
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
    matches AS (
      SELECT DISTINCT ON (l.id) l.id, l.type, l.population, l.lat, l.lng, l.parent_id
      FROM location_name ln
      JOIN location l ON l.id = ln.location_id
      JOIN active_locs al ON al.id = l.id
      WHERE ln.locale = ${locale}
        AND lower(ln.name) LIKE ${q + "%"}
      ORDER BY l.id
    )
    SELECT m.id,
      dn.name,
      m.type::text AS type,
      pdn.name AS parent_name,
      COALESCE(m.population, 0) AS population,
      m.lat, m.lng
    FROM matches m
    JOIN location_name dn
      ON dn.location_id = m.id AND dn.locale = ${locale} AND dn.is_display = true
    LEFT JOIN location_name pdn
      ON pdn.location_id = m.parent_id AND pdn.locale = ${locale} AND pdn.is_display = true
  `);

  type Row = { id: number; name: string; type: string; parent_name: string | null; population: number; lat: number | null; lng: number | null };
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
      const nearA = a.dist < NEAR_KM;
      const nearB = b.dist < NEAR_KM;
      if (nearA && nearB) return a.dist - b.dist;
      if (nearA !== nearB) return nearA ? -1 : 1;
      return b.population - a.population;
    })
    .slice(0, 8);

  return sorted.map((r) => ({
    id: r.id,
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
