import { sql } from "drizzle-orm";
import { db } from "@/db";
import type {
  PostingLocation,
  SearchFilters,
  SearchProvider,
  SearchResponse,
  SearchResultCompany,
  SearchResultPosting,
  HistogramFilters,
  SalaryBucket,
  ExperienceBucket,
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
  is_active: boolean | null;
  relevance_score: number;
  total_companies: number;
  location_ids: number[] | null;
  location_types: string[] | null;
}

/**
 * Resolve location IDs to display names in a single batch query.
 */
interface ResolvedLoc {
  name: string;
  geoType: "city" | "region" | "country" | "macro";
}

async function resolveLocationNames(
  ids: number[],
  locale: string,
): Promise<Map<number, ResolvedLoc>> {
  if (ids.length === 0) return new Map();
  const pgArray = `{${ids.join(",")}}`;
  const rows = await db.execute<{
    [key: string]: unknown;
    location_id: number;
    name: string;
    type: string;
  }>(sql`
    SELECT DISTINCT ON (ln.location_id) ln.location_id, ln.name, l.type::text
    FROM location_name ln
    JOIN location l ON l.id = ln.location_id
    WHERE ln.location_id = ANY(${pgArray}::integer[])
      AND ln.locale IN (${locale}, 'en')
      AND ln.is_display = true
    ORDER BY ln.location_id, (ln.locale = ${locale})::int DESC
  `);
  const map = new Map<number, ResolvedLoc>();
  for (const r of rows as unknown as { location_id: number; name: string; type: string }[]) {
    map.set(r.location_id, { name: r.name, geoType: r.type as ResolvedLoc["geoType"] });
  }
  return map;
}

/**
 * Build PostingLocation[] from raw IDs/types, ordering matching locations first.
 */
function buildPostingLocations(
  locationIds: number[] | null,
  locationTypes: string[] | null,
  nameMap: Map<number, ResolvedLoc>,
  filterIds?: Set<number>,
): PostingLocation[] {
  if (!locationIds || locationIds.length === 0) return [];
  const locs: PostingLocation[] = [];
  for (let i = 0; i < locationIds.length; i++) {
    const resolved = nameMap.get(locationIds[i]);
    if (resolved) {
      locs.push({
        name: resolved.name,
        type: locationTypes?.[i] ?? "onsite",
        geoType: resolved.geoType,
      });
    }
  }
  if (filterIds && filterIds.size > 0) {
    // Move locations matching the filter to the front
    locs.sort((a, b) => {
      const aMatch = [...filterIds].some((fid) => nameMap.get(fid)?.name === a.name);
      const bMatch = [...filterIds].some((fid) => nameMap.get(fid)?.name === b.name);
      if (aMatch && !bMatch) return -1;
      if (!aMatch && bMatch) return 1;
      return 0;
    });
  }
  return locs;
}

function groupRows(
  rows: RawSearchRow[],
  nameMap: Map<number, ResolvedLoc>,
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
        isActive: row.is_active == null ? undefined : Boolean(row.is_active),
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

/** Escape PostgreSQL regex special characters */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Build a word-boundary regex pattern for a keyword.
 *  Uses \m (word start) / \M (word end) so "Rust" matches but "Trust" doesn't. */
function wordPattern(keyword: string): string {
  const escaped = escapeRegex(keyword);
  const startBound = /^\w/.test(keyword) ? "\\m" : "";
  const endBound = /\w$/.test(keyword) ? "\\M" : "";
  return `${startBound}${escaped}${endBound}`;
}

/** Build OR condition — word-boundary regex for matching keywords in titles */
function matchOr(alias: string, keywords: string[]) {
  return sql.join(
    keywords.map((k) => {
      return sql`${sql.raw(alias)}.titles[1] ~* ${wordPattern(k)}`;
    }),
    sql` OR `,
  );
}

/** Count keyword hits via word-boundary regex matching */
function keywordCountExpr(_vecRef: string, titleRef: string, keywords: string[]) {
  return sql.join(
    keywords.map((k) => {
      return sql`CASE WHEN ${sql.raw(titleRef)} ~* ${wordPattern(k)} THEN 1 ELSE 0 END`;
    }),
    sql` + `,
  );
}

function locationFilter(alias: string, locationIds?: number[]) {
  if (!locationIds || locationIds.length === 0) return sql`true`;
  const pgArray = `{${locationIds.join(",")}}`;
  return sql`${sql.raw(alias)}.location_ids && ${pgArray}::integer[]`;
}

function occupationFilter(alias: string, occupationIds?: number[]) {
  if (!occupationIds || occupationIds.length === 0) return sql`true`;
  const pgArray = `{${occupationIds.join(",")}}`;
  return sql`${sql.raw(alias)}.occupation_id = ANY(${pgArray}::integer[])`;
}

function seniorityFilter(alias: string, seniorityIds?: number[]) {
  if (!seniorityIds || seniorityIds.length === 0) return sql`true`;
  const pgArray = `{${seniorityIds.join(",")}}`;
  return sql`${sql.raw(alias)}.seniority_id = ANY(${pgArray}::integer[])`;
}

function technologyFilter(alias: string, technologyIds?: number[]) {
  if (!technologyIds || technologyIds.length === 0) return sql`true`;
  const pgArray = `{${technologyIds.join(",")}}`;
  return sql`${sql.raw(alias)}.technology_ids && ${pgArray}::integer[]`;
}

function employmentTypeFilter(alias: string, employmentTypes?: string[]) {
  if (!employmentTypes || employmentTypes.length === 0) return sql`true`;
  const pgArray = `{${employmentTypes.join(",")}}`;
  return sql`${sql.raw(alias)}.employment_type = ANY(${pgArray}::text[])`;
}

/** Filter by job language(s). Empty array = no filter (all languages). */
function languageFilter(alias: string, languages: string[]) {
  if (languages.length === 0) return sql`true`;
  const pgArray = `{${languages.join(",")}}`;
  return sql`(${sql.raw(alias)}.locales && ${pgArray}::text[] OR ${sql.raw(alias)}.locales = '{}')`;
}

/** Filter by salary range (EUR-normalized). Uses the pre-computed salary_eur column. */
function salaryFilter(alias: string, minEur?: number, maxEur?: number) {
  if (minEur == null && maxEur == null) return sql`true`;
  if (minEur != null && maxEur != null) {
    return sql`${sql.raw(alias)}.salary_eur BETWEEN ${minEur} AND ${maxEur}`;
  }
  if (minEur != null) {
    return sql`${sql.raw(alias)}.salary_eur >= ${minEur}`;
  }
  return sql`${sql.raw(alias)}.salary_eur <= ${maxEur!}`;
}

/** Filter by experience range. Jobs without stated requirements are NOT excluded by maxYears. */
function experienceFilter(alias: string, minYears?: number, maxYears?: number) {
  if (minYears == null && maxYears == null) return sql`true`;
  if (minYears != null && maxYears != null) {
    return sql`(${sql.raw(alias)}.experience_min IS NULL OR (${sql.raw(alias)}.experience_min >= ${minYears} AND ${sql.raw(alias)}.experience_min <= ${maxYears}))`;
  }
  if (minYears != null) {
    return sql`(${sql.raw(alias)}.experience_min IS NULL OR ${sql.raw(alias)}.experience_min >= ${minYears})`;
  }
  return sql`(${sql.raw(alias)}.experience_min IS NULL OR ${sql.raw(alias)}.experience_min <= ${maxYears!})`;
}

export class PostgresSearchProvider implements SearchProvider {
  async search(params: SearchFilters & {
    keywords: string[];
    offset: number;
    limit: number;
  }): Promise<SearchResponse> {
    const { keywords, languages, locale, offset, limit, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes, salaryMinEur, salaryMaxEur, experienceMin, experienceMax } = params;

    const rows = await db.execute<RawSearchRow>(sql`
      WITH posting_matches AS (
        SELECT jp.id, jp.company_id, jp.titles[1] AS title, jp.first_seen_at, jp.is_active,
          jp.location_ids, jp.location_types,
          (${keywordCountExpr("", "jp.titles[1]", keywords)}) AS keyword_count
        FROM job_posting jp
        WHERE (jp.is_active = true OR jp.first_seen_at >= now() - interval '1 year')
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${matchOr("jp", keywords)})
          AND (${languageFilter("jp", languages)})
          AND (${locationFilter("jp", locationIds)})
          AND (${occupationFilter("jp", occupationIds)})
          AND (${seniorityFilter("jp", seniorityIds)})
          AND (${technologyFilter("jp", technologyIds)})
          AND (${employmentTypeFilter("jp", employmentTypes)})
          AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp", experienceMin, experienceMax)})
      ),
      matched_companies AS (
        SELECT
          pm.company_id,
          COUNT(*) FILTER (WHERE pm.is_active) AS active_matches,
          COUNT(*) FILTER (WHERE pm.first_seen_at >= now() - interval '1 year') AS year_matches,
          MAX(pm.keyword_count) FILTER (WHERE pm.is_active) AS best_keyword_count
        FROM posting_matches pm
        GROUP BY pm.company_id
        HAVING COUNT(*) FILTER (WHERE pm.is_active) > 0
        ORDER BY best_keyword_count DESC, active_matches DESC, year_matches DESC, pm.company_id
        LIMIT ${limit} OFFSET ${offset}
      ),
      total AS (
        SELECT COUNT(*) AS cnt FROM (SELECT DISTINCT pm.company_id FROM posting_matches pm WHERE pm.is_active) sub
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
        p.is_active,
        COALESCE(p.keyword_count, 0) AS relevance_score,
        t.cnt AS total_companies,
        p.location_ids,
        p.location_types
      FROM matched_companies mc
      JOIN company c ON c.id = mc.company_id
      CROSS JOIN total t
      LEFT JOIN LATERAL (
        SELECT pm2.id, pm2.title, pm2.first_seen_at, pm2.is_active, pm2.keyword_count,
          pm2.location_ids, pm2.location_types
        FROM posting_matches pm2
        WHERE pm2.company_id = mc.company_id
          AND pm2.is_active = true
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
    const nameMap = await resolveLocationNames([...allLocIds], locale);
    return groupRows(rawRows, nameMap, locationIds);
  }

  async listTopCompanies(params: SearchFilters & {
    offset: number;
    limit: number;
  }): Promise<SearchResponse> {
    const { languages, locale, offset, limit, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes, salaryMinEur, salaryMaxEur, experienceMin, experienceMax } = params;

    const rows = await db.execute<RawSearchRow>(sql`
      WITH all_companies AS (
        SELECT
          jp.company_id,
          COUNT(*) FILTER (WHERE jp.is_active) AS active_matches,
          COUNT(*) FILTER (WHERE jp.first_seen_at >= now() - interval '1 year') AS year_matches
        FROM job_posting jp
        WHERE (jp.is_active = true OR jp.first_seen_at >= now() - interval '1 year')
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${languageFilter("jp", languages)})
          AND (${locationFilter("jp", locationIds)})
          AND (${occupationFilter("jp", occupationIds)})
          AND (${seniorityFilter("jp", seniorityIds)})
          AND (${technologyFilter("jp", technologyIds)})
          AND (${employmentTypeFilter("jp", employmentTypes)})
          AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp", experienceMin, experienceMax)})
        GROUP BY jp.company_id
        HAVING COUNT(*) FILTER (WHERE jp.is_active) > 0
      ),
      top_companies AS (
        SELECT * FROM all_companies
        ORDER BY active_matches DESC, year_matches DESC, company_id
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
        p.is_active,
        0 AS relevance_score,
        (SELECT COUNT(*) FROM all_companies) AS total_companies,
        p.location_ids,
        p.location_types
      FROM top_companies tc
      JOIN company c ON c.id = tc.company_id
      LEFT JOIN LATERAL (
        SELECT jp2.id, jp2.titles[1] AS title, jp2.first_seen_at, jp2.is_active,
          jp2.location_ids, jp2.location_types
        FROM job_posting jp2
        WHERE jp2.company_id = tc.company_id
          AND jp2.is_active = true
          AND jp2.titles[1] IS NOT NULL AND jp2.titles[1] != ''
          AND (${languageFilter("jp2", languages)})
          AND (${locationFilter("jp2", locationIds)})
          AND (${occupationFilter("jp2", occupationIds)})
          AND (${seniorityFilter("jp2", seniorityIds)})
          AND (${technologyFilter("jp2", technologyIds)})
          AND (${employmentTypeFilter("jp2", employmentTypes)})
          AND (${salaryFilter("jp2", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp2", experienceMin, experienceMax)})
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
    const nameMap = await resolveLocationNames([...allLocIds], locale);
    return groupRows(rawRows, nameMap, locationIds);
  }

  async loadPostings(params: SearchFilters & {
    companyId: string;
    keywords: string[];
    offset: number;
    limit: number;
  }): Promise<SearchResultPosting[]> {
    const { companyId, keywords, languages, locale, offset, limit, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes, salaryMinEur, salaryMaxEur, experienceMin, experienceMax } = params;

    interface PostingRow {
      [key: string]: unknown;
      id: string;
      title: string | null;
      first_seen_at: Date;
      is_active: boolean;
      keyword_count: number;
      location_ids: number[] | null;
      location_types: string[] | null;
    }

    let rawRows: PostingRow[];

    if (keywords.length > 0) {
      const rows = await db.execute<PostingRow>(sql`
        SELECT jp.id, jp.titles[1] AS title, jp.first_seen_at, jp.is_active,
          (${keywordCountExpr("", "jp.titles[1]", keywords)}) AS keyword_count,
          jp.location_ids, jp.location_types
        FROM job_posting jp
        WHERE jp.company_id = ${companyId}
          AND jp.is_active = true
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${matchOr("jp", keywords)})
          AND (${languageFilter("jp", languages)})
          AND (${locationFilter("jp", locationIds)})
          AND (${occupationFilter("jp", occupationIds)})
          AND (${seniorityFilter("jp", seniorityIds)})
          AND (${technologyFilter("jp", technologyIds)})
          AND (${employmentTypeFilter("jp", employmentTypes)})
          AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp", experienceMin, experienceMax)})
        ORDER BY keyword_count DESC, jp.first_seen_at DESC, jp.id
        LIMIT ${limit} OFFSET ${offset}
      `);
      rawRows = rows as unknown as PostingRow[];
    } else {
      const rows = await db.execute<PostingRow>(sql`
        SELECT jp.id, jp.titles[1] AS title, jp.first_seen_at, jp.is_active,
          0 AS keyword_count,
          jp.location_ids, jp.location_types
        FROM job_posting jp
        WHERE jp.company_id = ${companyId}
          AND jp.is_active = true
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${languageFilter("jp", languages)})
          AND (${locationFilter("jp", locationIds)})
          AND (${occupationFilter("jp", occupationIds)})
          AND (${seniorityFilter("jp", seniorityIds)})
          AND (${technologyFilter("jp", technologyIds)})
          AND (${employmentTypeFilter("jp", employmentTypes)})
          AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp", experienceMin, experienceMax)})
        ORDER BY jp.first_seen_at DESC, jp.id
        LIMIT ${limit} OFFSET ${offset}
      `);
      rawRows = rows as unknown as PostingRow[];
    }

    const allLocIds = new Set<number>();
    for (const r of rawRows) {
      if (r.location_ids) for (const id of r.location_ids) allLocIds.add(id);
    }
    const nameMap = await resolveLocationNames([...allLocIds], locale);
    const filterSet = locationIds && locationIds.length > 0
      ? new Set(locationIds)
      : undefined;

    return rawRows.map((r) => ({
      id: r.id,
      title: r.title,
      firstSeenAt: new Date(r.first_seen_at),
      isActive: true,
      relevanceScore: Number(r.keyword_count),
      locations: buildPostingLocations(
        r.location_ids,
        r.location_types,
        nameMap,
        filterSet,
      ),
    }));
  }

  async loadPostingsWithCounts(params: SearchFilters & {
    companyId: string;
    keywords: string[];
    offset: number;
    limit: number;
  }): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number }> {
    const { companyId, keywords, languages, locale, offset, limit, locationIds, occupationIds, seniorityIds, technologyIds, employmentTypes, salaryMinEur, salaryMaxEur, experienceMin, experienceMax } = params;

    interface CountRow {
      [key: string]: unknown;
      active_count: number;
      year_count: number;
    }

    interface PostingRow {
      [key: string]: unknown;
      id: string;
      title: string | null;
      first_seen_at: Date;
      is_active: boolean;
      keyword_count: number;
      location_ids: number[] | null;
      location_types: string[] | null;
    }

    // Counts query
    const countRows = await db.execute<CountRow>(sql`
      SELECT
        COUNT(*) FILTER (WHERE jp.is_active) AS active_count,
        COUNT(*) FILTER (WHERE jp.first_seen_at >= now() - interval '1 year') AS year_count
      FROM job_posting jp
      WHERE jp.company_id = ${companyId}
        AND (jp.is_active = true OR jp.first_seen_at >= now() - interval '1 year')
        AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
        AND (${languageFilter("jp", languages)})
        ${keywords.length > 0 ? sql`AND (${matchOr("jp", keywords)})` : sql``}
        AND (${locationFilter("jp", locationIds)})
        AND (${occupationFilter("jp", occupationIds)})
        AND (${seniorityFilter("jp", seniorityIds)})
        AND (${technologyFilter("jp", technologyIds)})
        AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
        AND (${experienceFilter("jp", experienceMin, experienceMax)})
    `);
    const counts = (countRows as unknown as CountRow[])[0];
    const activeCount = Number(counts?.active_count ?? 0);
    const yearCount = Number(counts?.year_count ?? 0);

    // Postings query (same as loadPostings)
    let rawRows: PostingRow[];

    if (keywords.length > 0) {
      const rows = await db.execute<PostingRow>(sql`
        SELECT jp.id, jp.titles[1] AS title, jp.first_seen_at, jp.is_active,
          (${keywordCountExpr("", "jp.titles[1]", keywords)}) AS keyword_count,
          jp.location_ids, jp.location_types
        FROM job_posting jp
        WHERE jp.company_id = ${companyId}
          AND jp.is_active = true
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${matchOr("jp", keywords)})
          AND (${languageFilter("jp", languages)})
          AND (${locationFilter("jp", locationIds)})
          AND (${occupationFilter("jp", occupationIds)})
          AND (${seniorityFilter("jp", seniorityIds)})
          AND (${technologyFilter("jp", technologyIds)})
          AND (${employmentTypeFilter("jp", employmentTypes)})
          AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp", experienceMin, experienceMax)})
        ORDER BY keyword_count DESC, jp.first_seen_at DESC, jp.id
        LIMIT ${limit} OFFSET ${offset}
      `);
      rawRows = rows as unknown as PostingRow[];
    } else {
      const rows = await db.execute<PostingRow>(sql`
        SELECT jp.id, jp.titles[1] AS title, jp.first_seen_at, jp.is_active,
          0 AS keyword_count,
          jp.location_ids, jp.location_types
        FROM job_posting jp
        WHERE jp.company_id = ${companyId}
          AND jp.is_active = true
          AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
          AND (${languageFilter("jp", languages)})
          AND (${locationFilter("jp", locationIds)})
          AND (${occupationFilter("jp", occupationIds)})
          AND (${seniorityFilter("jp", seniorityIds)})
          AND (${technologyFilter("jp", technologyIds)})
          AND (${employmentTypeFilter("jp", employmentTypes)})
          AND (${salaryFilter("jp", salaryMinEur, salaryMaxEur)})
          AND (${experienceFilter("jp", experienceMin, experienceMax)})
        ORDER BY jp.first_seen_at DESC, jp.id
        LIMIT ${limit} OFFSET ${offset}
      `);
      rawRows = rows as unknown as PostingRow[];
    }

    const allLocIds = new Set<number>();
    for (const r of rawRows) {
      if (r.location_ids) for (const id of r.location_ids) allLocIds.add(id);
    }
    const nameMap = await resolveLocationNames([...allLocIds], locale);
    const filterSet = locationIds && locationIds.length > 0
      ? new Set(locationIds)
      : undefined;

    const postings = rawRows.map((r) => ({
      id: r.id,
      title: r.title,
      firstSeenAt: new Date(r.first_seen_at),
      isActive: true,
      relevanceScore: Number(r.keyword_count),
      locations: buildPostingLocations(
        r.location_ids,
        r.location_types,
        nameMap,
        filterSet,
      ),
    }));

    return { postings, activeCount, yearCount };
  }

  async getSalaryHistogram(filters?: HistogramFilters): Promise<SalaryBucket[]> {
    const f = filters ?? {};
    const hasKeywords = f.keywords && f.keywords.length > 0;
    const rows = await db.execute<{ [key: string]: unknown; bucket: number; cnt: number }>(sql`
      SELECT
        width_bucket(jp.salary_eur, 0, 300000, 30) AS bucket,
        COUNT(*)::int AS cnt
      FROM job_posting jp
      WHERE jp.is_active = true
        AND jp.salary_eur IS NOT NULL AND jp.salary_eur > 0
        AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
        ${f.companyId ? sql`AND jp.company_id = ${f.companyId}` : sql``}
        ${hasKeywords ? sql`AND (${matchOr("jp", f.keywords!)})` : sql``}
        AND (${locationFilter("jp", f.locationIds)})
        AND (${occupationFilter("jp", f.occupationIds)})
        AND (${seniorityFilter("jp", f.seniorityIds)})
        AND (${technologyFilter("jp", f.technologyIds)})
        AND (${languageFilter("jp", f.languages ?? [])})
      GROUP BY bucket
      ORDER BY bucket
    `);
    const bucketWidth = 10000;
    return (rows as unknown as { bucket: number; cnt: number }[]).map((r) => ({
      min: (r.bucket - 1) * bucketWidth,
      max: r.bucket * bucketWidth,
      count: r.cnt,
    }));
  }

  async getExperienceHistogram(filters?: HistogramFilters): Promise<ExperienceBucket[]> {
    const f = filters ?? {};
    const hasKeywords = f.keywords && f.keywords.length > 0;
    const rows = await db.execute<{ [key: string]: unknown; years: number; cnt: number }>(sql`
      SELECT jp.experience_min AS years, COUNT(*)::int AS cnt
      FROM job_posting jp
      WHERE jp.is_active = true
        AND jp.experience_min IS NOT NULL
        AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
        ${f.companyId ? sql`AND jp.company_id = ${f.companyId}` : sql``}
        ${hasKeywords ? sql`AND (${matchOr("jp", f.keywords!)})` : sql``}
        AND (${locationFilter("jp", f.locationIds)})
        AND (${occupationFilter("jp", f.occupationIds)})
        AND (${seniorityFilter("jp", f.seniorityIds)})
        AND (${technologyFilter("jp", f.technologyIds)})
        AND (${languageFilter("jp", f.languages ?? [])})
      GROUP BY jp.experience_min
      ORDER BY jp.experience_min
    `);
    return (rows as unknown as { years: number; cnt: number }[]).map((r) => ({
      years: r.years,
      count: r.cnt,
    }));
  }
}
