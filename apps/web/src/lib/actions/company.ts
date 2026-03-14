"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { getSearchProvider } from "@/lib/search";
import type { SearchResultPosting } from "@/lib/search";
import { cached } from "@/lib/cache";
import { expandLocationIds } from "@/lib/actions/locations";

// ── Company suggestions (search bar autocomplete) ───────────────────

export interface CompanySuggestion {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
}

export async function suggestCompanies(params: {
  query: string;
}): Promise<CompanySuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];

  const key = `company-suggest:${q}`;
  return cached(key, () => _queryCompanySuggestions(q), { ttl: 600 });
}

async function _queryCompanySuggestions(q: string): Promise<CompanySuggestion[]> {
  const rows = await db.execute<{
    [key: string]: unknown;
    id: string;
    name: string;
    slug: string;
    icon: string | null;
    match_rank: number;
  }>(sql`
    WITH prefix_matches AS (
      SELECT c.id, c.name, c.slug, c.icon, 1 AS match_rank
      FROM company c
      WHERE lower(c.name) LIKE ${q + "%"}
        AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.company_id = c.id AND jp.is_active = true)
      LIMIT 5
    ),
    fuzzy_matches AS (
      SELECT c.id, c.name, c.slug, c.icon, 2 AS match_rank
      FROM company c
      WHERE length(${q}) >= 3
        AND similarity(lower(c.name), ${q}) > 0.3
        AND c.id NOT IN (SELECT id FROM prefix_matches)
        AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.company_id = c.id AND jp.is_active = true)
      ORDER BY similarity(lower(c.name), ${q}) DESC
      LIMIT 5
    )
    SELECT * FROM prefix_matches
    UNION ALL
    SELECT * FROM fuzzy_matches
    LIMIT 5
  `);

  type Row = { id: string; name: string; slug: string; icon: string | null; match_rank: number };
  return (rows as unknown as Row[]).map((r) => ({
    id: r.id,
    name: r.name,
    slug: r.slug,
    icon: r.icon,
  }));
}

// ── Company detail ──────────────────────────────────────────────────

export interface CompanyDetail {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  website: string | null;
  description: string | null;
  industryName: string | null;
  employeeCountRange: number | null;
  foundedYear: number | null;
}

export async function getCompanyBySlug(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  const key = `company-slug:${slug}:${locale}`;
  return cached(key, () => _fetchCompanyBySlug(slug), { ttl: 600 });
}

async function _fetchCompanyBySlug(slug: string): Promise<CompanyDetail | null> {
  const rows = await db.execute<{
    [key: string]: unknown;
    id: string;
    name: string;
    slug: string;
    icon: string | null;
    website: string | null;
    description: string | null;
    industry_name: string | null;
    employee_count_range: number | null;
    founded_year: number | null;
  }>(sql`
    SELECT c.id, c.name, c.slug, c.icon, c.website, c.description,
      i.name AS industry_name,
      c.employee_count_range,
      c.founded_year
    FROM company c
    LEFT JOIN industry i ON i.id = c.industry
    WHERE c.slug = ${slug}
  `);

  type Row = {
    id: string; name: string; slug: string; icon: string | null;
    website: string | null; description: string | null;
    industry_name: string | null; employee_count_range: number | null;
    founded_year: number | null;
  };
  const row = (rows as unknown as Row[])[0];
  if (!row) return null;

  return {
    id: row.id,
    name: row.name,
    slug: row.slug,
    icon: row.icon,
    website: row.website,
    description: row.description,
    industryName: row.industry_name,
    employeeCountRange: row.employee_count_range,
    foundedYear: row.founded_year,
  };
}

// ── Company postings with counts ────────────────────────────────────

export async function getCompanyPostings(params: {
  companyId: string;
  keywords: string[];
  locationIds?: number[];
  language: string;
  offset: number;
  limit: number;
}): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number }> {
  const sortedKw = [...params.keywords].sort();
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const key = `company-postings:${params.companyId}:${sortedKw.join(",")}:${sortedLoc.join(",")}:${params.language}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const expandedIds = await resolveLocationIds(params.locationIds);
      return getSearchProvider().loadPostingsWithCounts({ ...params, locationIds: expandedIds });
    },
    { ttl: 300 },
  );
}

// ── Top locations for a company ─────────────────────────────────────

export interface CompanyLocation {
  id: number;
  slug: string;
  name: string;
  type: string;
  count: number;
}

export async function getCompanyTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  const key = `company-top-locs:${companyId}:${locale}`;
  return cached(key, () => _fetchTopLocations(companyId, locale), { ttl: 600 });
}

async function _fetchTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  const rows = await db.execute<{
    [key: string]: unknown;
    location_id: number;
    loc_slug: string;
    loc_type: string;
    loc_name: string;
    cnt: number;
    total_locations: number;
  }>(sql`
    WITH active_locs AS (
      SELECT unnest(jp.location_ids) AS location_id
      FROM job_posting jp
      WHERE jp.company_id = ${companyId}
        AND jp.is_active = true
        AND jp.location_ids IS NOT NULL
    ),
    grouped AS (
      SELECT
        al.location_id,
        l.slug AS loc_slug,
        l.type::text AS loc_type,
        ln.name AS loc_name,
        COUNT(*)::int AS cnt
      FROM active_locs al
      JOIN location l ON l.id = al.location_id
      JOIN LATERAL (
        SELECT name FROM location_name
        WHERE location_id = al.location_id AND locale IN (${locale}, 'en') AND is_display = true
        ORDER BY (locale = ${locale})::int DESC LIMIT 1
      ) ln ON true
      GROUP BY al.location_id, l.slug, l.type, ln.name
    )
    SELECT *, COUNT(*) OVER ()::int AS total_locations
    FROM grouped
    ORDER BY cnt DESC
    LIMIT 15
  `);

  type Row = { location_id: number; loc_slug: string; loc_type: string; loc_name: string; cnt: number; total_locations: number };
  const all = rows as unknown as Row[];
  return {
    locations: all.map((r) => ({
      id: r.location_id,
      slug: r.loc_slug,
      name: r.loc_name,
      type: r.loc_type,
      count: r.cnt,
    })),
    totalCount: all[0]?.total_locations ?? 0,
  };
}

// ── All locations grouped by country / region ─────────────────────

export interface CompanyLocationWithAliases extends CompanyLocation {
  aliases: string[];
}

export interface CompanyRegionGroup {
  regionId: number;
  regionSlug: string;
  regionName: string;
  regionCount: number;
  regionAliases: string[];
  locations: CompanyLocationWithAliases[];
}

export interface GroupedCompanyLocations {
  countryId: number;
  countrySlug: string;
  countryName: string;
  countryCount: number;
  countryAliases: string[];
  regions: CompanyRegionGroup[];
}

export async function getCompanyLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  const key = `company-locs-grouped:${companyId}:${locale}`;
  return cached(key, () => _fetchLocationsGrouped(companyId, locale), { ttl: 600 });
}

async function _fetchLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  const rows = await db.execute<{
    [key: string]: unknown;
    location_id: number;
    loc_slug: string;
    loc_type: string;
    loc_name: string;
    cnt: number;
    region_id: number | null;
    region_slug: string | null;
    region_name: string | null;
    country_id: number | null;
    country_slug: string | null;
    country_name: string | null;
  }>(sql`
    WITH active_locs AS (
      SELECT unnest(jp.location_ids) AS location_id
      FROM job_posting jp
      WHERE jp.company_id = ${companyId}
        AND jp.is_active = true
        AND jp.location_ids IS NOT NULL
    ),
    loc_counts AS (
      SELECT al.location_id, COUNT(*)::int AS cnt
      FROM active_locs al GROUP BY al.location_id
    ),
    hierarchy AS (
      SELECT lc.location_id, lc.cnt,
        l.type::text AS loc_type, l.slug AS loc_slug,
        CASE
          WHEN l.type = 'region' THEN l.id
          WHEN l.type = 'city' AND p.type = 'region' THEN p.id
          ELSE NULL
        END AS region_id,
        CASE
          WHEN l.type = 'country' THEN l.id
          WHEN p.type = 'country' THEN p.id
          WHEN gp.type = 'country' THEN gp.id
          ELSE NULL
        END AS country_id
      FROM loc_counts lc
      JOIN location l ON l.id = lc.location_id
      LEFT JOIN location p ON p.id = l.parent_id
      LEFT JOIN location gp ON gp.id = p.parent_id
    )
    SELECT
      h.location_id, h.loc_slug, h.loc_type, h.cnt,
      ln.name AS loc_name,
      h.region_id,
      rl.slug AS region_slug,
      rn.name AS region_name,
      h.country_id,
      cl.slug AS country_slug,
      cn.name AS country_name
    FROM hierarchy h
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = h.location_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) ln ON true
    LEFT JOIN location rl ON rl.id = h.region_id
    LEFT JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = h.region_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) rn ON true
    LEFT JOIN location cl ON cl.id = h.country_id
    LEFT JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = h.country_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) cn ON true
    ORDER BY cn.name NULLS LAST, rn.name NULLS LAST, h.cnt DESC
  `);

  type Row = {
    location_id: number; loc_slug: string; loc_type: string; loc_name: string; cnt: number;
    region_id: number | null; region_slug: string | null; region_name: string | null;
    country_id: number | null; country_slug: string | null; country_name: string | null;
  };

  // Collect all location IDs for alias lookup
  const allLocIds = new Set<number>();
  for (const r of rows as unknown as Row[]) {
    allLocIds.add(r.location_id);
    if (r.region_id) allLocIds.add(r.region_id);
    if (r.country_id) allLocIds.add(r.country_id);
  }

  // Fetch name aliases (user locale + en)
  const aliasMap = new Map<number, string[]>();
  if (allLocIds.size > 0) {
    const pgArray = `{${[...allLocIds].join(",")}}`;
    const aliasRows = await db.execute<{
      [key: string]: unknown;
      location_id: number;
      name: string;
    }>(sql`
      SELECT location_id, lower(name) AS name
      FROM location_name
      WHERE location_id = ANY(${pgArray}::integer[])
        AND locale IN (${locale}, 'en')
    `);
    for (const a of aliasRows as unknown as { location_id: number; name: string }[]) {
      let arr = aliasMap.get(a.location_id);
      if (!arr) { arr = []; aliasMap.set(a.location_id, arr); }
      if (!arr.includes(a.name)) arr.push(a.name);
    }
  }

  // Build country → region → city hierarchy
  const countries = new Map<number, GroupedCompanyLocations>();
  // Track direct counts for country/region entries
  const directCountryCount = new Map<number, number>();
  const directRegionCount = new Map<number, number>();

  for (const r of rows as unknown as Row[]) {
    const cid = r.country_id ?? 0;
    let country = countries.get(cid);
    if (!country) {
      country = {
        countryId: cid,
        countrySlug: r.country_slug ?? "",
        countryName: r.country_name ?? "Other",
        countryCount: 0,
        countryAliases: aliasMap.get(cid) ?? [],
        regions: [],
      };
      countries.set(cid, country);
    }

    if (r.loc_type === "country") {
      directCountryCount.set(cid, r.cnt);
      continue;
    }
    if (r.loc_type === "region") {
      directRegionCount.set(r.location_id, r.cnt);
      continue;
    }

    // City: find or create region group
    const rid = r.region_id ?? 0;
    let region = country.regions.find((rg) => rg.regionId === rid);
    if (!region) {
      region = {
        regionId: rid,
        regionSlug: r.region_slug ?? "",
        regionName: r.region_name ?? "",
        regionCount: 0,
        regionAliases: rid > 0 ? (aliasMap.get(rid) ?? []) : [],
        locations: [],
      };
      country.regions.push(region);
    }

    region.locations.push({
      id: r.location_id,
      slug: r.loc_slug,
      name: r.loc_name,
      type: r.loc_type,
      count: r.cnt,
      aliases: aliasMap.get(r.location_id) ?? [],
    });
  }

  // Aggregate counts bottom-up
  for (const country of countries.values()) {
    let countryTotal = directCountryCount.get(country.countryId) ?? 0;
    for (const region of country.regions) {
      const cityTotal = region.locations.reduce((sum, l) => sum + l.count, 0);
      region.regionCount = cityTotal + (directRegionCount.get(region.regionId) ?? 0);
      countryTotal += region.regionCount;
    }
    country.countryCount = countryTotal;
    // Sort regions by count desc
    country.regions.sort((a, b) => b.regionCount - a.regionCount);
  }

  return [...countries.values()].filter((g) => g.regions.some((r) => r.locations.length > 0));
}

// ── Helpers ─────────────────────────────────────────────────────────

async function resolveLocationIds(
  locationIds?: number[],
): Promise<number[] | undefined> {
  if (!locationIds || locationIds.length === 0) return undefined;
  const expanded = await Promise.all(locationIds.map(expandLocationIds));
  return [...new Set(expanded.flat())];
}
