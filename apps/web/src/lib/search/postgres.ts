import { sql } from "drizzle-orm";
import { db } from "@/db";
import type {
  PostingLocation,
  SearchProvider,
  SearchResponse,
  SearchResultCompany,
  SearchResultPosting,
} from "./types";

interface RawSearchRow {
  [key: string]: unknown;
  company_id: string;
  company_name: string;
  company_slug: string;
  company_icon: string | null;
  active_matches: number;
  year_matches: number;
  posting_id: string;
  posting_title: string | null;
  first_seen_at: Date;
  relevance_score: number;
  total_companies: number;
  location_ids: number[] | null;
  location_types: string[] | null;
}

/**
 * Resolve location IDs to display names in a single batch query.
 */
async function resolveLocationNames(
  ids: number[],
  locale: string,
): Promise<Map<number, string>> {
  if (ids.length === 0) return new Map();
  const pgArray = `{${ids.join(",")}}`;
  const rows = await db.execute<{
    [key: string]: unknown;
    location_id: number;
    name: string;
  }>(sql`
    SELECT location_id, name
    FROM location_name
    WHERE location_id = ANY(${pgArray}::integer[])
      AND locale = ${locale}
      AND is_display = true
  `);
  const map = new Map<number, string>();
  for (const r of rows as unknown as { location_id: number; name: string }[]) {
    map.set(r.location_id, r.name);
  }
  return map;
}

/**
 * Build PostingLocation[] from raw IDs/types, ordering matching locations first.
 */
function buildPostingLocations(
  locationIds: number[] | null,
  locationTypes: string[] | null,
  nameMap: Map<number, string>,
  filterIds?: Set<number>,
): PostingLocation[] {
  if (!locationIds || locationIds.length === 0) return [];
  const locs: PostingLocation[] = [];
  for (let i = 0; i < locationIds.length; i++) {
    const name = nameMap.get(locationIds[i]);
    if (name) {
      locs.push({ name, type: locationTypes?.[i] ?? "onsite" });
    }
  }
  if (filterIds && filterIds.size > 0) {
    // Move locations matching the filter to the front
    locs.sort((a, b) => {
      const aMatch = [...filterIds].some((fid) => nameMap.get(fid) === a.name);
      const bMatch = [...filterIds].some((fid) => nameMap.get(fid) === b.name);
      if (aMatch && !bMatch) return -1;
      if (!aMatch && bMatch) return 1;
      return 0;
    });
  }
  return locs;
}

function groupRows(
  rows: RawSearchRow[],
  nameMap: Map<number, string>,
  filterLocationIds?: number[],
): SearchResponse {
  if (rows.length === 0) return { companies: [], totalCompanies: 0 };

  const totalCompanies = Number(rows[0].total_companies);
  const filterSet = filterLocationIds && filterLocationIds.length > 0
    ? new Set(filterLocationIds)
    : undefined;
  const map = new Map<string, SearchResultCompany>();

  for (const row of rows) {
    let entry = map.get(row.company_id);
    if (!entry) {
      entry = {
        company: {
          id: row.company_id,
          name: row.company_name,
          slug: row.company_slug,
          icon: row.company_icon,
        },
        activeMatches: Number(row.active_matches),
        yearMatches: Number(row.year_matches),
        postings: [],
      };
      map.set(row.company_id, entry);
    }
    if (row.posting_id && !entry.postings.some((p) => p.id === row.posting_id)) {
      entry.postings.push({
        id: row.posting_id,
        title: row.posting_title,
        firstSeenAt: new Date(row.first_seen_at),
        relevanceScore: Number(row.relevance_score),
        locations: buildPostingLocations(
          row.location_ids,
          row.location_types,
          nameMap,
          filterSet,
        ),
      });
    }
  }

  return { companies: Array.from(map.values()), totalCompanies };
}

// Must match the GIN index expression (titles[1] + employment_type)
function tsvecFor(alias: string) {
  return sql.raw(`(
    setweight(to_tsvector('simple'::regconfig, coalesce(${alias}.titles[1], '')), 'A') ||
    setweight(to_tsvector('simple'::regconfig, coalesce(${alias}.employment_type, '')), 'D')
  )`);
}

/** Build OR condition — uses the GIN index for fast filtering */
function matchOr(alias: string, keywords: string[]) {
  return sql.join(
    keywords.map((k) => sql`${tsvecFor(alias)} @@ plainto_tsquery('simple'::regconfig, ${k})`),
    sql` OR `,
  );
}

/** Count keyword hits against a pre-computed tsvector column (no regexp_replace) */
function keywordCountFromVec(vecRef: string, keywords: string[]) {
  const col = sql.raw(vecRef);
  return sql.join(
    keywords.map(
      (k) => sql`CASE WHEN ${col} @@ plainto_tsquery('simple'::regconfig, ${k}) THEN 1 ELSE 0 END`,
    ),
    sql` + `,
  );
}

function locationFilter(alias: string, locationIds?: number[]) {
  if (!locationIds || locationIds.length === 0) return sql`true`;
  const pgArray = `{${locationIds.join(",")}}`;
  return sql`${sql.raw(alias)}.location_ids && ${pgArray}::integer[]`;
}

export class PostgresSearchProvider implements SearchProvider {
  async search(params: {
    keywords: string[];
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<SearchResponse> {
    const { keywords, language, offset, limit, locationIds } = params;

    const rows = await db.execute<RawSearchRow>(sql`
      WITH filtered AS (
        SELECT jp.id, jp.company_id, jp.titles[1] AS title, jp.first_seen_at, jp.is_active,
          jp.location_ids, jp.location_types,
          ${tsvecFor("jp")} AS vec
        FROM job_posting jp
        WHERE jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${matchOr("jp", keywords)})
          AND (${language} = ANY(jp.locales) OR jp.locales = '{}')
          AND (${locationFilter("jp", locationIds)})
      ),
      posting_matches AS (
        SELECT f.id, f.company_id, f.title, f.first_seen_at, f.is_active,
          f.location_ids, f.location_types,
          (${keywordCountFromVec("f.vec", keywords)}) AS keyword_count
        FROM filtered f
      ),
      matched_companies AS (
        SELECT
          pm.company_id,
          COUNT(*) FILTER (WHERE pm.is_active) AS active_matches,
          COUNT(*) AS year_matches,
          MAX(pm.keyword_count) AS best_keyword_count
        FROM posting_matches pm
        GROUP BY pm.company_id
        ORDER BY best_keyword_count DESC, active_matches DESC, year_matches DESC
        LIMIT ${limit} OFFSET ${offset}
      ),
      total AS (
        SELECT COUNT(DISTINCT pm.company_id) AS cnt
        FROM posting_matches pm
      )
      SELECT
        mc.company_id,
        c.name AS company_name,
        c.slug AS company_slug,
        c.icon AS company_icon,
        mc.active_matches,
        mc.year_matches,
        p.id AS posting_id,
        p.title AS posting_title,
        p.first_seen_at,
        COALESCE(p.keyword_count, 0) AS relevance_score,
        t.cnt AS total_companies,
        p.location_ids,
        p.location_types
      FROM matched_companies mc
      JOIN company c ON c.id = mc.company_id
      CROSS JOIN total t
      LEFT JOIN LATERAL (
        SELECT pm2.id, pm2.title, pm2.first_seen_at, pm2.keyword_count,
          pm2.location_ids, pm2.location_types
        FROM posting_matches pm2
        WHERE pm2.company_id = mc.company_id
        ORDER BY pm2.keyword_count DESC, pm2.first_seen_at DESC
        LIMIT 10
      ) p ON true
      ORDER BY mc.best_keyword_count DESC, mc.active_matches DESC, mc.year_matches DESC
    `);

    const rawRows = rows as unknown as RawSearchRow[];
    const allLocIds = new Set<number>();
    for (const r of rawRows) {
      if (r.location_ids) for (const id of r.location_ids) allLocIds.add(id);
    }
    const nameMap = await resolveLocationNames([...allLocIds], language);
    return groupRows(rawRows, nameMap, locationIds);
  }

  async listTopCompanies(params: {
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<SearchResponse> {
    const { language, offset, limit, locationIds } = params;

    const rows = await db.execute<RawSearchRow>(sql`
      WITH all_companies AS (
        SELECT
          jp.company_id,
          COUNT(*) FILTER (WHERE jp.is_active) AS active_matches,
          COUNT(*) AS year_matches
        FROM job_posting jp
        WHERE jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${language} = ANY(jp.locales) OR jp.locales = '{}')
          AND (${locationFilter("jp", locationIds)})
        GROUP BY jp.company_id
        HAVING COUNT(*) FILTER (WHERE jp.is_active) > 0
      ),
      top_companies AS (
        SELECT * FROM all_companies
        ORDER BY active_matches DESC, year_matches DESC
        LIMIT ${limit} OFFSET ${offset}
      )
      SELECT
        tc.company_id,
        c.name AS company_name,
        c.slug AS company_slug,
        c.icon AS company_icon,
        tc.active_matches,
        tc.year_matches,
        p.id AS posting_id,
        p.title AS posting_title,
        p.first_seen_at,
        0 AS relevance_score,
        (SELECT COUNT(*) FROM all_companies) AS total_companies,
        p.location_ids,
        p.location_types
      FROM top_companies tc
      JOIN company c ON c.id = tc.company_id
      LEFT JOIN LATERAL (
        SELECT jp2.id, jp2.titles[1] AS title, jp2.first_seen_at,
          jp2.location_ids, jp2.location_types
        FROM job_posting jp2
        WHERE jp2.company_id = tc.company_id
          AND jp2.titles[1] IS NOT NULL AND jp2.titles[1] != ''
          AND (${language} = ANY(jp2.locales) OR jp2.locales = '{}')
          AND (${locationFilter("jp2", locationIds)})
        ORDER BY jp2.first_seen_at DESC
        LIMIT 10
      ) p ON true
      ORDER BY tc.active_matches DESC, tc.year_matches DESC
    `);

    const rawRows = rows as unknown as RawSearchRow[];
    const allLocIds = new Set<number>();
    for (const r of rawRows) {
      if (r.location_ids) for (const id of r.location_ids) allLocIds.add(id);
    }
    const nameMap = await resolveLocationNames([...allLocIds], language);
    return groupRows(rawRows, nameMap, locationIds);
  }

  async loadPostings(params: {
    companyId: string;
    keywords: string[];
    locationIds?: number[];
    language: string;
    offset: number;
    limit: number;
  }): Promise<SearchResultPosting[]> {
    const { companyId, keywords, language, offset, limit, locationIds } = params;

    interface PostingRow {
      [key: string]: unknown;
      id: string;
      title: string | null;
      first_seen_at: Date;
      keyword_count: number;
      location_ids: number[] | null;
      location_types: string[] | null;
    }

    let rawRows: PostingRow[];

    if (keywords.length > 0) {
      const rows = await db.execute<PostingRow>(sql`
        SELECT sub.id, sub.title, sub.first_seen_at,
          (${keywordCountFromVec("sub.vec", keywords)}) AS keyword_count,
          sub.location_ids, sub.location_types
        FROM (
          SELECT jp.id, jp.titles[1] AS title, jp.first_seen_at,
            jp.location_ids, jp.location_types,
            ${tsvecFor("jp")} AS vec
          FROM job_posting jp
          WHERE jp.company_id = ${companyId}
            AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
            AND (${matchOr("jp", keywords)})
            AND (${language} = ANY(jp.locales) OR jp.locales = '{}')
            AND (${locationFilter("jp", locationIds)})
        ) sub
        ORDER BY keyword_count DESC, sub.first_seen_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `);
      rawRows = rows as unknown as PostingRow[];
    } else {
      const rows = await db.execute<PostingRow>(sql`
        SELECT jp.id, jp.titles[1] AS title, jp.first_seen_at,
          0 AS keyword_count,
          jp.location_ids, jp.location_types
        FROM job_posting jp
        WHERE jp.company_id = ${companyId}
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${language} = ANY(jp.locales) OR jp.locales = '{}')
          AND (${locationFilter("jp", locationIds)})
        ORDER BY jp.first_seen_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `);
      rawRows = rows as unknown as PostingRow[];
    }

    const allLocIds = new Set<number>();
    for (const r of rawRows) {
      if (r.location_ids) for (const id of r.location_ids) allLocIds.add(id);
    }
    const nameMap = await resolveLocationNames([...allLocIds], language);
    const filterSet = locationIds && locationIds.length > 0
      ? new Set(locationIds)
      : undefined;

    return rawRows.map((r) => ({
      id: r.id,
      title: r.title,
      firstSeenAt: new Date(r.first_seen_at),
      relevanceScore: Number(r.keyword_count),
      locations: buildPostingLocations(
        r.location_ids,
        r.location_types,
        nameMap,
        filterSet,
      ),
    }));
  }
}
