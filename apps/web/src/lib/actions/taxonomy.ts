"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";

export interface TaxonomySuggestion {
  id: number;
  slug: string;
  name: string;
  /** The alias that matched the query (if different from display name). */
  matchedName?: string;
}

export async function suggestOccupations(params: {
  query: string;
  locale: string;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];

  const key = `occ-suggest:${q}:${params.locale}`;
  return cached(key, () => _queryOccupationSuggestions(q, params.locale), { ttl: 3600 });
}

async function _queryOccupationSuggestions(
  q: string,
  locale: string,
): Promise<TaxonomySuggestion[]> {
  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    name: string;
    match_rank: number;
  }>(sql`
    WITH prefix_matches AS (
      SELECT DISTINCT ON (o.id) o.id, o.slug, 1 AS match_rank
      FROM occupation_name otn
      JOIN occupation o ON o.id = otn.occupation_id
      WHERE lower(otn.name) LIKE ${q + "%"}
        AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.occupation_id = o.id AND jp.is_active = true)
      ORDER BY o.id
    ),
    fuzzy_matches AS (
      SELECT DISTINCT ON (o.id) o.id, o.slug, 2 AS match_rank
      FROM occupation_name otn
      JOIN occupation o ON o.id = otn.occupation_id
      WHERE length(${q}) >= 3
        AND similarity(lower(otn.name), ${q}) > 0.25
        AND o.id NOT IN (SELECT id FROM prefix_matches)
        AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.occupation_id = o.id AND jp.is_active = true)
      ORDER BY o.id, similarity(lower(otn.name), ${q}) DESC
    ),
    matches AS (
      SELECT * FROM prefix_matches
      UNION ALL
      SELECT * FROM fuzzy_matches
    )
    SELECT m.id, m.slug, dn.name, mn.matched_name, m.match_rank
    FROM matches m
    JOIN LATERAL (
      SELECT name FROM occupation_name
      WHERE occupation_id = m.id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) dn ON true
    JOIN LATERAL (
      SELECT otn.name AS matched_name FROM occupation_name otn
      WHERE otn.occupation_id = m.id
        AND (lower(otn.name) LIKE ${q + "%"} OR (length(${q}) >= 3 AND similarity(lower(otn.name), ${q}) > 0.25))
      ORDER BY (lower(otn.name) LIKE ${q + "%"})::int DESC, similarity(lower(otn.name), ${q}) DESC
      LIMIT 1
    ) mn ON true
    ORDER BY m.match_rank, m.slug
    LIMIT 5
  `);

  return (rows as unknown as { id: number; slug: string; name: string; matched_name: string }[]).map((r) => ({
    id: r.id,
    slug: r.slug,
    name: r.name,
    matchedName: r.matched_name !== r.name ? r.matched_name : undefined,
  }));
}

export async function suggestSeniorities(params: {
  query: string;
  locale: string;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];

  const key = `sen-suggest:${q}:${params.locale}`;
  return cached(key, () => _querySenioritySuggestions(q, params.locale), { ttl: 3600 });
}

async function _querySenioritySuggestions(
  q: string,
  locale: string,
): Promise<TaxonomySuggestion[]> {
  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    name: string;
    match_rank: number;
  }>(sql`
    WITH prefix_matches AS (
      SELECT DISTINCT ON (s.id) s.id, s.slug, 1 AS match_rank
      FROM seniority_name stn
      JOIN seniority s ON s.id = stn.seniority_id
      WHERE lower(stn.name) LIKE ${q + "%"}
        AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.seniority_id = s.id AND jp.is_active = true)
      ORDER BY s.id
    ),
    fuzzy_matches AS (
      SELECT DISTINCT ON (s.id) s.id, s.slug, 2 AS match_rank
      FROM seniority_name stn
      JOIN seniority s ON s.id = stn.seniority_id
      WHERE length(${q}) >= 3
        AND similarity(lower(stn.name), ${q}) > 0.25
        AND s.id NOT IN (SELECT id FROM prefix_matches)
        AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.seniority_id = s.id AND jp.is_active = true)
      ORDER BY s.id, similarity(lower(stn.name), ${q}) DESC
    ),
    matches AS (
      SELECT * FROM prefix_matches
      UNION ALL
      SELECT * FROM fuzzy_matches
    )
    SELECT m.id, m.slug, dn.name, mn.matched_name, m.match_rank
    FROM matches m
    JOIN LATERAL (
      SELECT name FROM seniority_name
      WHERE seniority_id = m.id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) dn ON true
    JOIN LATERAL (
      SELECT stn2.name AS matched_name FROM seniority_name stn2
      WHERE stn2.seniority_id = m.id
        AND (lower(stn2.name) LIKE ${q + "%"} OR (length(${q}) >= 3 AND similarity(lower(stn2.name), ${q}) > 0.25))
      ORDER BY (lower(stn2.name) LIKE ${q + "%"})::int DESC, similarity(lower(stn2.name), ${q}) DESC
      LIMIT 1
    ) mn ON true
    ORDER BY m.match_rank, m.slug
    LIMIT 5
  `);

  return (rows as unknown as { id: number; slug: string; name: string; matched_name: string }[]).map((r) => ({
    id: r.id,
    slug: r.slug,
    name: r.name,
    matchedName: r.matched_name !== r.name ? r.matched_name : undefined,
  }));
}

export async function resolveOccupationSlugs(
  slugs: string[],
  locale: string,
): Promise<Map<string, TaxonomySuggestion>> {
  if (slugs.length === 0) return new Map();
  const key = `occ-resolve:${slugs.sort().join(",")}:${locale}`;
  const record = await cached(
    key,
    async () => {
      const pgArray = `{${slugs.join(",")}}`;
      const rows = await db.execute<{
        [key: string]: unknown;
        id: number;
        slug: string;
        name: string;
      }>(sql`
        SELECT o.id, o.slug, dn.name
        FROM occupation o
        JOIN LATERAL (
          SELECT name FROM occupation_name
          WHERE occupation_id = o.id AND locale IN (${locale}, 'en') AND is_display = true
          ORDER BY (locale = ${locale})::int DESC LIMIT 1
        ) dn ON true
        WHERE o.slug = ANY(${pgArray}::text[])
      `);
      const result: Record<string, TaxonomySuggestion> = {};
      for (const r of rows as unknown as { id: number; slug: string; name: string }[]) {
        result[r.slug] = { id: r.id, slug: r.slug, name: r.name };
      }
      return result;
    },
    { ttl: 3600 },
  );
  return new Map(Object.entries(record));
}

export async function resolveSenioritySlugs(
  slugs: string[],
  locale: string,
): Promise<Map<string, TaxonomySuggestion>> {
  if (slugs.length === 0) return new Map();
  const key = `sen-resolve:${slugs.sort().join(",")}:${locale}`;
  const record = await cached(
    key,
    async () => {
      const pgArray = `{${slugs.join(",")}}`;
      const rows = await db.execute<{
        [key: string]: unknown;
        id: number;
        slug: string;
        name: string;
      }>(sql`
        SELECT s.id, s.slug, dn.name
        FROM seniority s
        JOIN LATERAL (
          SELECT name FROM seniority_name
          WHERE seniority_id = s.id AND locale IN (${locale}, 'en') AND is_display = true
          ORDER BY (locale = ${locale})::int DESC LIMIT 1
        ) dn ON true
        WHERE s.slug = ANY(${pgArray}::text[])
      `);
      const result: Record<string, TaxonomySuggestion> = {};
      for (const r of rows as unknown as { id: number; slug: string; name: string }[]) {
        result[r.slug] = { id: r.id, slug: r.slug, name: r.name };
      }
      return result;
    },
    { ttl: 3600 },
  );
  return new Map(Object.entries(record));
}

/**
 * Expand an occupation ID to include all descendant (child) IDs.
 * If "Software Engineer" is selected, also match "Frontend Developer", "Backend Developer", etc.
 */
export async function expandOccupationIds(occupationId: number): Promise<number[]> {
  const key = `occ-expand:${occupationId}`;
  return cached(
    key,
    async () => {
      const rows = await db.execute<{ [key: string]: unknown; id: number }>(sql`
        WITH RECURSIVE descendants AS (
          SELECT id FROM occupation WHERE id = ${occupationId}
          UNION ALL
          SELECT o.id FROM occupation o JOIN descendants d ON o.parent_id = d.id
        )
        SELECT id FROM descendants
      `);
      return (rows as unknown as { id: number }[]).map((r) => r.id);
    },
    { ttl: 86400 },
  );
}

// ── All occupations grouped by domain ─────────────────────────────────

export interface OccupationItem {
  id: number;
  slug: string;
  name: string;
  count: number;
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
): Promise<OccupationGroup[]> {
  const fKey = filters ? JSON.stringify(filters) : "";
  const key = `occ-all-grouped:${locale}:${fKey}`;
  return cached(key, () => _fetchAllOccupationsGrouped(locale, filters), { ttl: 3600 });
}

async function _fetchAllOccupationsGrouped(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<OccupationGroup[]> {
  const f = filters;
  const hasKeywords = f?.keywords && f.keywords.length > 0;
  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    name: string;
    cnt: number;
    parent_id: number | null;
    domain_id: number | null;
    domain_slug: string | null;
    domain_name: string | null;
  }>(sql`
    WITH occ_counts AS (
      SELECT jp.occupation_id, COUNT(*)::int AS cnt
      FROM job_posting jp
      WHERE jp.is_active = true AND jp.occupation_id IS NOT NULL
        AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
        ${f?.companyId ? sql`AND jp.company_id = ${f.companyId}` : sql``}
        ${hasKeywords ? sql`AND (${sql.join(f!.keywords!.map(k => sql`jp.titles[1] ~* ${`\\m${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\M`}`), sql` OR `)})` : sql``}
        ${f?.locationIds && f.locationIds.length > 0 ? sql`AND jp.location_ids && ${`{${f.locationIds.join(",")}}`}::integer[]` : sql``}
        ${f?.seniorityIds && f.seniorityIds.length > 0 ? sql`AND jp.seniority_id = ANY(${`{${f.seniorityIds.join(",")}}`}::integer[])` : sql``}
        ${f?.technologyIds && f.technologyIds.length > 0 ? sql`AND jp.technology_ids && ${`{${f.technologyIds.join(",")}}`}::integer[]` : sql``}
        ${f?.languages && f.languages.length > 0 ? sql`AND (jp.locales && ${`{${f.languages.join(",")}}`}::text[] OR jp.locales = '{}')` : sql``}
      GROUP BY jp.occupation_id
    ),
    relevant AS (
      SELECT o.id, o.slug, o.parent_id, o.domain_id, COALESCE(oc.cnt, 0) AS cnt
      FROM occupation o
      LEFT JOIN occ_counts oc ON oc.occupation_id = o.id
      WHERE oc.cnt > 0
        OR EXISTS (
          SELECT 1 FROM occupation child
          JOIN occ_counts cc ON cc.occupation_id = child.id
          WHERE child.parent_id = o.id
        )
    )
    SELECT
      r.id, r.slug, dn.name, r.cnt,
      r.parent_id,
      r.domain_id,
      od.slug AS domain_slug,
      odn.name AS domain_name
    FROM relevant r
    JOIN LATERAL (
      SELECT name FROM occupation_name
      WHERE occupation_id = r.id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) dn ON true
    LEFT JOIN occupation_domain od ON od.id = r.domain_id
    LEFT JOIN LATERAL (
      SELECT name FROM occupation_domain_name
      WHERE domain_id = r.domain_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) odn ON r.domain_id IS NOT NULL
    ORDER BY r.cnt DESC
  `);

  type Row = {
    id: number; slug: string; name: string; cnt: number;
    parent_id: number | null;
    domain_id: number | null; domain_slug: string | null; domain_name: string | null;
  };
  const all = rows as unknown as Row[];

  // Collect all rows into domain buckets
  const domainRows = new Map<number, { meta: { id: number; slug: string; name: string }; rows: Row[] }>();
  const ungrouped: OccupationGroup[] = [];

  for (const r of all) {
    if (r.domain_id != null && r.domain_slug && r.domain_name) {
      let bucket = domainRows.get(r.domain_id);
      if (!bucket) {
        bucket = { meta: { id: r.domain_id, slug: r.domain_slug, name: r.domain_name }, rows: [] };
        domainRows.set(r.domain_id, bucket);
      }
      bucket.rows.push(r);
    } else {
      ungrouped.push({
        domain: { id: r.id, slug: r.slug, name: r.name, count: r.cnt },
        subGroups: [],
        standalone: [{ id: r.id, slug: r.slug, name: r.name, count: r.cnt }],
      });
    }
  }

  // Build OccupationGroup per domain with parent-child sub-groups
  const result: OccupationGroup[] = [];

  for (const { meta, rows: domainItems } of domainRows.values()) {
    const idSet = new Set(domainItems.map((r) => r.id));
    const parentIds = new Set(domainItems.filter((r) => r.parent_id != null && idSet.has(r.parent_id)).map((r) => r.parent_id!));

    const subGroupMap = new Map<number, OccupationSubGroup>();
    const standalone: OccupationItem[] = [];

    // First pass: create sub-groups for parents
    for (const r of domainItems) {
      if (parentIds.has(r.id)) {
        subGroupMap.set(r.id, {
          parent: { id: r.id, slug: r.slug, name: r.name, count: r.cnt },
          children: [],
        });
      }
    }

    // Second pass: assign children and standalone
    for (const r of domainItems) {
      if (r.parent_id != null && subGroupMap.has(r.parent_id)) {
        subGroupMap.get(r.parent_id)!.children.push({ id: r.id, slug: r.slug, name: r.name, count: r.cnt });
      } else if (!parentIds.has(r.id)) {
        standalone.push({ id: r.id, slug: r.slug, name: r.name, count: r.cnt });
      }
    }

    // Sort sub-groups by total count, children within by count
    const subGroups = [...subGroupMap.values()].sort((a, b) => {
      const aTotal = a.parent.count + a.children.reduce((s, c) => s + c.count, 0);
      const bTotal = b.parent.count + b.children.reduce((s, c) => s + c.count, 0);
      return bTotal - aTotal;
    });
    for (const sg of subGroups) {
      sg.children.sort((a, b) => b.count - a.count);
    }
    standalone.sort((a, b) => b.count - a.count);

    const totalCount = domainItems.reduce((s, r) => s + r.cnt, 0);
    result.push({
      domain: { id: meta.id, slug: meta.slug, name: meta.name, count: totalCount },
      subGroups,
      standalone,
    });
  }

  result.sort((a, b) => b.domain.count - a.domain.count);
  return [...result, ...ungrouped];
}

// ── All seniorities ──────────────────────────────────────────────────

export interface SeniorityOption {
  id: number;
  slug: string;
  name: string;
  count: number;
}

export async function getAllSeniorities(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<SeniorityOption[]> {
  const fKey = filters ? JSON.stringify(filters) : "";
  const key = `sen-all:${locale}:${fKey}`;
  return cached(key, () => _fetchAllSeniorities(locale, filters), { ttl: 3600 });
}

async function _fetchAllSeniorities(
  locale: string,
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; technologyIds?: number[]; languages?: string[] },
): Promise<SeniorityOption[]> {
  const f = filters;
  const hasKeywords = f?.keywords && f.keywords.length > 0;
  const rows = await db.execute<{
    [key: string]: unknown;
    id: number;
    slug: string;
    name: string;
    cnt: number;
  }>(sql`
    WITH sen_counts AS (
      SELECT jp.seniority_id, COUNT(*)::int AS cnt
      FROM job_posting jp
      WHERE jp.is_active = true AND jp.seniority_id IS NOT NULL
        AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
        ${f?.companyId ? sql`AND jp.company_id = ${f.companyId}` : sql``}
        ${hasKeywords ? sql`AND (${sql.join(f!.keywords!.map(k => sql`jp.titles[1] ~* ${`\\m${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\M`}`), sql` OR `)})` : sql``}
        ${f?.locationIds && f.locationIds.length > 0 ? sql`AND jp.location_ids && ${`{${f.locationIds.join(",")}}`}::integer[]` : sql``}
        ${f?.occupationIds && f.occupationIds.length > 0 ? sql`AND jp.occupation_id = ANY(${`{${f.occupationIds.join(",")}}`}::integer[])` : sql``}
        ${f?.technologyIds && f.technologyIds.length > 0 ? sql`AND jp.technology_ids && ${`{${f.technologyIds.join(",")}}`}::integer[]` : sql``}
        ${f?.languages && f.languages.length > 0 ? sql`AND (jp.locales && ${`{${f.languages.join(",")}}`}::text[] OR jp.locales = '{}')` : sql``}
      GROUP BY jp.seniority_id
    )
    SELECT s.id, s.slug, dn.name, sc.cnt
    FROM sen_counts sc
    JOIN seniority s ON s.id = sc.seniority_id
    JOIN LATERAL (
      SELECT name FROM seniority_name
      WHERE seniority_id = s.id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) dn ON true
    ORDER BY s.id
  `);

  type Row = { id: number; slug: string; name: string; cnt: number };
  return (rows as unknown as Row[]).map((r) => ({
    id: r.id,
    slug: r.slug,
    name: r.name,
    count: r.cnt,
  }));
}

// ── Technology suggestions ──────────────────────────────────────────

export async function suggestTechnologies(params: {
  query: string;
  locale: string;
}): Promise<TaxonomySuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];
  const key = `tech-suggest:${q}`;
  return cached(key, () => _queryTechnologySuggestions(q), { ttl: 3600 });
}

async function _queryTechnologySuggestions(q: string): Promise<TaxonomySuggestion[]> {
  const rows = await db.execute<{
    [key: string]: unknown; id: number; slug: string; name: string;
  }>(sql`
    SELECT t.id, t.slug, COALESCE(t.name, t.slug) AS name
    FROM technology t
    WHERE (lower(t.slug) LIKE ${q + "%"} OR lower(t.name) LIKE ${q + "%"})
      AND EXISTS (
        SELECT 1 FROM job_posting jp
        WHERE jp.technology_ids @> ARRAY[t.id]
          AND jp.is_active = true
      )
    ORDER BY t.slug
    LIMIT 5
  `);
  return (rows as unknown as { id: number; slug: string; name: string }[]).map((r) => ({
    id: r.id, slug: r.slug, name: r.name,
  }));
}

export async function resolveTechnologySlugs(
  slugs: string[],
): Promise<Map<string, TaxonomySuggestion>> {
  if (slugs.length === 0) return new Map();
  const key = `tech-resolve:${slugs.sort().join(",")}`;
  const record = await cached(key, async () => {
    const pgArray = `{${slugs.join(",")}}`;
    const rows = await db.execute<{
      [key: string]: unknown; id: number; slug: string; name: string;
    }>(sql`
      SELECT t.id, t.slug, COALESCE(t.name, t.slug) AS name
      FROM technology t
      WHERE t.slug = ANY(${pgArray}::text[])
    `);
    const result: Record<string, TaxonomySuggestion> = {};
    for (const r of rows as unknown as { id: number; slug: string; name: string }[]) {
      result[r.slug] = { id: r.id, slug: r.slug, name: r.name };
    }
    return result;
  }, { ttl: 3600 });
  return new Map(Object.entries(record));
}

// ── All technologies grouped by category ────────────────────────────

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
): Promise<TechnologyGroup[]> {
  const fKey = filters ? JSON.stringify(filters) : "";
  const key = `tech-all-grouped:${fKey}`;
  return cached(key, () => _fetchAllTechnologiesGrouped(filters), { ttl: 3600 });
}

async function _fetchAllTechnologiesGrouped(
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; occupationIds?: number[]; seniorityIds?: number[]; languages?: string[] },
): Promise<TechnologyGroup[]> {
  const f = filters;
  const hasKeywords = f?.keywords && f.keywords.length > 0;
  const rows = await db.execute<{
    [key: string]: unknown; id: number; slug: string; name: string; category: string; cnt: number;
  }>(sql`
    WITH tech_counts AS (
      SELECT unnest(jp.technology_ids) AS tech_id, COUNT(*)::int AS cnt
      FROM job_posting jp
      WHERE jp.is_active = true AND jp.technology_ids IS NOT NULL
        AND jp.titles[1] IS NOT NULL AND jp.titles[1] != ''
        ${f?.companyId ? sql`AND jp.company_id = ${f.companyId}` : sql``}
        ${hasKeywords ? sql`AND (${sql.join(f!.keywords!.map(k => sql`jp.titles[1] ~* ${`\\m${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\M`}`), sql` OR `)})` : sql``}
        ${f?.locationIds && f.locationIds.length > 0 ? sql`AND jp.location_ids && ${`{${f.locationIds.join(",")}}`}::integer[]` : sql``}
        ${f?.occupationIds && f.occupationIds.length > 0 ? sql`AND jp.occupation_id = ANY(${`{${f.occupationIds.join(",")}}`}::integer[])` : sql``}
        ${f?.seniorityIds && f.seniorityIds.length > 0 ? sql`AND jp.seniority_id = ANY(${`{${f.seniorityIds.join(",")}}`}::integer[])` : sql``}
        ${f?.languages && f.languages.length > 0 ? sql`AND (jp.locales && ${`{${f.languages.join(",")}}`}::text[] OR jp.locales = '{}')` : sql``}
      GROUP BY tech_id
    )
    SELECT t.id, t.slug, COALESCE(t.name, t.slug) AS name,
           COALESCE(t.category, 'other') AS category, tc.cnt
    FROM tech_counts tc
    JOIN technology t ON t.id = tc.tech_id
    ORDER BY tc.cnt DESC
  `);
  type Row = { id: number; slug: string; name: string; category: string; cnt: number };
  const all = rows as unknown as Row[];

  const groups = new Map<string, TechnologyItem[]>();
  for (const r of all) {
    const items = groups.get(r.category) ?? [];
    items.push({ id: r.id, slug: r.slug, name: r.name, count: r.cnt });
    groups.set(r.category, items);
  }

  return [...groups.entries()]
    .map(([category, technologies]) => ({ category, technologies }))
    .sort((a, b) => {
      const aTotal = a.technologies.reduce((s, t) => s + t.count, 0);
      const bTotal = b.technologies.reduce((s, t) => s + t.count, 0);
      return bTotal - aTotal;
    });
}
