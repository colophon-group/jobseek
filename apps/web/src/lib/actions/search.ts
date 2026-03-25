"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { getSearchProvider } from "@/lib/search";
import type { SearchResponse, SearchResultPosting, HistogramFilters } from "@/lib/search";
import { cached } from "@/lib/cache";
import { expandLocationIds } from "@/lib/actions/locations";
import { expandOccupationIds } from "@/lib/actions/taxonomy";

// ── Posting detail ──────────────────────────────────────────────────

export interface PostingDetail {
  id: string;
  title: string | null;
  company: { id: string; name: string; slug: string; logo: string | null; icon: string | null };
  locations: { id: number; name: string; type: string; geoType?: string; parentName?: string }[];
  employmentType: string | null;
  experienceMin: number | null;
  experienceMax: number | null;
  technologies: { id: number; name: string }[];
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string | null;
  salaryPeriod: string | null;
  seniority: { id: number; slug: string; name: string } | null;
  sourceUrl: string;
  firstSeenAt: string;
  descriptionHtml: string | null;
  descriptionUrl: string | null;
}

export async function getPostingDetail(params: {
  postingId: string;
  locale: string;
}): Promise<PostingDetail | null> {
  const { postingId, locale } = params;
  const key = `posting-detail:${postingId}:${locale}`;
  return cached(key, () => _fetchPostingDetail(postingId, locale), { ttl: 300 });
}

async function resolvePostingLocations(
  locationIds: number[] | null,
  locationTypes: string[] | null,
  locale: string,
): Promise<PostingDetail["locations"]> {
  if (!locationIds || locationIds.length === 0) return [];
  const pgArray = `{${locationIds.join(",")}}`;
  const locRows = await db.execute<{
    [key: string]: unknown;
    location_id: number;
    name: string;
    type: string;
    parent_name: string | null;
  }>(sql`
    SELECT DISTINCT ON (ln.location_id) ln.location_id, ln.name, l.type::text,
      (SELECT pn.name FROM location_name pn
       WHERE pn.location_id = l.parent_id
         AND pn.locale IN (${locale}, 'en')
         AND pn.is_display = true
       ORDER BY (pn.locale = ${locale})::int DESC
       LIMIT 1) AS parent_name
    FROM location_name ln
    JOIN location l ON l.id = ln.location_id
    WHERE ln.location_id = ANY(${pgArray}::integer[])
      AND ln.locale IN (${locale}, 'en')
      AND ln.is_display = true
    ORDER BY ln.location_id, (ln.locale = ${locale})::int DESC
  `);
  const nameMap = new Map<number, { name: string; geoType: string; parentName?: string }>();
  for (const r of locRows as unknown as { location_id: number; name: string; type: string; parent_name: string | null }[]) {
    nameMap.set(r.location_id, { name: r.name, geoType: r.type, parentName: r.parent_name ?? undefined });
  }
  return locationIds
    .map((id, i) => {
      const resolved = nameMap.get(id);
      return {
        id,
        name: resolved?.name ?? "",
        type: locationTypes?.[i] ?? "onsite",
        geoType: resolved?.geoType,
        parentName: resolved?.parentName,
      };
    })
    .filter((l) => l.name !== "");
}

async function resolvePostingTechnologies(
  technologyIds: number[] | null,
): Promise<{ id: number; name: string }[]> {
  if (!technologyIds || technologyIds.length === 0) return [];
  const techArray = `{${technologyIds.join(",")}}`;
  const techRows = await db.execute<{ [key: string]: unknown; id: number; name: string | null }>(
    sql`SELECT id, name FROM technology WHERE id = ANY(${techArray}::integer[]) ORDER BY name`,
  );
  return (techRows as unknown as { id: number; name: string | null }[])
    .filter((t) => t.name)
    .map((t) => ({ id: t.id, name: t.name! }));
}

async function _fetchPostingDetail(
  postingId: string,
  locale: string,
): Promise<PostingDetail | null> {
  const rows = await db.execute<{
    [key: string]: unknown;
    id: string;
    title: string | null;
    company_id: string;
    company_name: string;
    company_slug: string;
    company_logo: string | null;
    company_icon: string | null;
    location_ids: number[] | null;
    location_types: string[] | null;
    employment_type: string | null;
    source_url: string;
    first_seen_at: Date;
    locales: string[];
  }>(sql`
    SELECT jp.id, jp.titles[1] AS title,
      c.id AS company_id, c.name AS company_name, c.slug AS company_slug,
      c.logo AS company_logo, c.icon AS company_icon,
      jp.location_ids, jp.location_types,
      jp.employment_type, jp.source_url, jp.first_seen_at,
      jp.locales,
      jp.experience_min, jp.experience_max, jp.technology_ids,
      jp.salary_min, jp.salary_max, jp.salary_currency, jp.salary_period,
      jp.seniority_id, s.slug AS seniority_slug, sn.name AS seniority_name
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    LEFT JOIN seniority s ON s.id = jp.seniority_id
    LEFT JOIN LATERAL (
      SELECT name FROM seniority_name
      WHERE seniority_id = jp.seniority_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) sn ON true
    WHERE jp.id = ${postingId}
  `);

  type Row = {
    id: string; title: string | null;
    company_id: string; company_name: string; company_slug: string;
    company_logo: string | null; company_icon: string | null;
    location_ids: number[] | null; location_types: string[] | null;
    employment_type: string | null; source_url: string;
    first_seen_at: Date; locales: string[];
    experience_min: number | null; experience_max: number | null;
    technology_ids: number[] | null;
    salary_min: number | null; salary_max: number | null;
    salary_currency: string | null; salary_period: string | null;
    seniority_id: number | null; seniority_slug: string | null; seniority_name: string | null;
  };
  const row = (rows as unknown as Row[])[0];
  if (!row) return null;

  // Resolve location and technology names in parallel
  const [locations, technologies] = await Promise.all([
    resolvePostingLocations(row.location_ids, row.location_types, locale),
    resolvePostingTechnologies(row.technology_ids),
  ]);

  // Build R2 description URL for client-side fetch
  const r2Domain = process.env.R2_DOMAIN_URL;
  let descriptionUrl: string | null = null;
  if (r2Domain) {
    const descLocale = row.locales?.[0] ?? "en";
    descriptionUrl = `${r2Domain.replace(/\/$/, "")}/job/${postingId}/${descLocale}/latest.html`;
  }

  return {
    id: row.id,
    title: row.title,
    company: {
      id: row.company_id,
      name: row.company_name,
      slug: row.company_slug,
      logo: row.company_logo,
      icon: row.company_icon,
    },
    locations,
    employmentType: row.employment_type,
    experienceMin: row.experience_min,
    experienceMax: row.experience_max,
    technologies,
    salaryMin: row.salary_min,
    salaryMax: row.salary_max,
    salaryCurrency: row.salary_currency,
    salaryPeriod: row.salary_period,
    seniority: row.seniority_id && row.seniority_slug && row.seniority_name
      ? { id: row.seniority_id, slug: row.seniority_slug, name: row.seniority_name }
      : null,
    sourceUrl: row.source_url,
    firstSeenAt: new Date(row.first_seen_at).toISOString(),
    descriptionHtml: null,
    descriptionUrl,
  };
}

async function resolveLocationIds(
  locationIds?: number[],
): Promise<number[] | undefined> {
  if (!locationIds || locationIds.length === 0) return undefined;
  const expanded = await Promise.all(locationIds.map(expandLocationIds));
  return [...new Set(expanded.flat())];
}

async function resolveOccupationIds(
  occupationIds?: number[],
): Promise<number[] | undefined> {
  if (!occupationIds || occupationIds.length === 0) return undefined;
  const expanded = await Promise.all(occupationIds.map(expandOccupationIds));
  return [...new Set(expanded.flat())];
}

export async function searchJobs(params: {
  keywords: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages: string[];
  locale: string;
  offset: number;
  limit: number;
}): Promise<SearchResponse> {
  const sortedKw = [...params.keywords].sort();
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const sortedOcc = [...(params.occupationIds ?? [])].sort();
  const sortedSen = [...(params.seniorityIds ?? [])].sort();
  const sortedTech = [...(params.technologyIds ?? [])].sort().join(",");
  const sortedEtype = [...(params.employmentTypes ?? [])].sort().join(",");
  const sortedLangs = [...params.languages].sort();
  const salKey = `${params.salaryMinEur ?? ""}:${params.salaryMaxEur ?? ""}`;
  const expKey = `${params.experienceMax ?? ""}`;
  const key = `search:${sortedKw.join(",")}:${sortedLoc.join(",")}:${sortedOcc.join(",")}:${sortedSen.join(",")}:${sortedTech}:${sortedEtype}:${sortedLangs.join(",")}:${salKey}:${expKey}:${params.locale}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const [expandedLocs, expandedOccs] = await Promise.all([
        resolveLocationIds(params.locationIds),
        resolveOccupationIds(params.occupationIds),
      ]);
      return getSearchProvider().search({ ...params, locationIds: expandedLocs, occupationIds: expandedOccs });
    },
    { ttl: 300 },
  );
}

export async function listTopCompanies(params: {
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages: string[];
  locale: string;
  offset: number;
  limit: number;
}): Promise<SearchResponse> {
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const sortedOcc = [...(params.occupationIds ?? [])].sort();
  const sortedSen = [...(params.seniorityIds ?? [])].sort();
  const sortedTech = [...(params.technologyIds ?? [])].sort().join(",");
  const sortedEtype = [...(params.employmentTypes ?? [])].sort().join(",");
  const sortedLangs = [...params.languages].sort();
  const salKey = `${params.salaryMinEur ?? ""}:${params.salaryMaxEur ?? ""}`;
  const expKey = `${params.experienceMax ?? ""}`;
  const key = `top-companies:${sortedLoc.join(",")}:${sortedOcc.join(",")}:${sortedSen.join(",")}:${sortedTech}:${sortedEtype}:${sortedLangs.join(",")}:${salKey}:${expKey}:${params.locale}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const [expandedLocs, expandedOccs] = await Promise.all([
        resolveLocationIds(params.locationIds),
        resolveOccupationIds(params.occupationIds),
      ]);
      return getSearchProvider().listTopCompanies({ ...params, locationIds: expandedLocs, occupationIds: expandedOccs });
    },
    { ttl: 600 },
  );
}

// ── Currency rates for salary filter ────────────────────────────────

export interface CurrencyRate {
  currency: string;
  toEur: number;
}

export async function getCurrencyRates(): Promise<CurrencyRate[]> {
  try {
    return await cached(
      "currency-rates",
      async () => {
        const rows = await db.execute<{ [key: string]: unknown; currency: string; to_eur: string }>(
          sql`SELECT currency, to_eur FROM currency_rate ORDER BY currency`,
        );
        return (rows as unknown as { currency: string; to_eur: string }[]).map((r) => ({
          currency: r.currency,
          toEur: parseFloat(r.to_eur),
        }));
      },
      { ttl: 3600 },
    );
  } catch {
    // Table may not exist yet — return fallback without caching
    return [{ currency: "EUR", toEur: 1 }];
  }
}

export type { SalaryBucket, ExperienceBucket } from "@/lib/search/types";
import type { SalaryBucket, ExperienceBucket } from "@/lib/search/types";

/**
 * Returns salary_eur distribution across fixed EUR buckets for the histogram.
 * Accepts optional filters to scope to a company / keyword / location context.
 */
export async function getSalaryHistogram(filters?: HistogramFilters): Promise<SalaryBucket[]> {
  const f = filters ?? {};
  const keyParts = [
    "salary-histogram",
    f.companyId ?? "",
    [...(f.keywords ?? [])].sort().join(","),
    [...(f.locationIds ?? [])].sort().join(","),
    [...(f.occupationIds ?? [])].sort().join(","),
    [...(f.seniorityIds ?? [])].sort().join(","),
    [...(f.technologyIds ?? [])].sort().join(","),
    [...(f.languages ?? [])].sort().join(","),
  ];
  const key = keyParts.join(":");
  return cached(
    key,
    async () => {
      try {
        const [expandedLocs, expandedOccs] = await Promise.all([
          resolveLocationIds(f.locationIds),
          resolveOccupationIds(f.occupationIds),
        ]);
        return getSearchProvider().getSalaryHistogram({
          ...f,
          locationIds: expandedLocs,
          occupationIds: expandedOccs,
        });
      } catch {
        return [];
      }
    },
    { ttl: 3600 },
  );
}

/**
 * Returns experience_min distribution for the histogram.
 * Accepts optional filters to scope to a company / keyword / location context.
 */
export async function getExperienceHistogram(filters?: HistogramFilters): Promise<ExperienceBucket[]> {
  const f = filters ?? {};
  const keyParts = [
    "experience-histogram",
    f.companyId ?? "",
    [...(f.keywords ?? [])].sort().join(","),
    [...(f.locationIds ?? [])].sort().join(","),
    [...(f.occupationIds ?? [])].sort().join(","),
    [...(f.seniorityIds ?? [])].sort().join(","),
    [...(f.technologyIds ?? [])].sort().join(","),
    [...(f.languages ?? [])].sort().join(","),
  ];
  const key = keyParts.join(":");
  return cached(
    key,
    async () => {
      try {
        const [expandedLocs, expandedOccs] = await Promise.all([
          resolveLocationIds(f.locationIds),
          resolveOccupationIds(f.occupationIds),
        ]);
        return getSearchProvider().getExperienceHistogram({
          ...f,
          locationIds: expandedLocs,
          occupationIds: expandedOccs,
        });
      } catch {
        return [];
      }
    },
    { ttl: 3600 },
  );
}

// ── Load more postings ─────────────────────────────────────────────

export async function loadMorePostings(params: {
  companyId: string;
  keywords: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages: string[];
  locale: string;
  offset: number;
  limit: number;
}): Promise<SearchResultPosting[]> {
  const sortedKw = [...params.keywords].sort();
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const sortedOcc = [...(params.occupationIds ?? [])].sort();
  const sortedSen = [...(params.seniorityIds ?? [])].sort();
  const sortedTech = [...(params.technologyIds ?? [])].sort().join(",");
  const sortedLangs = [...params.languages].sort();
  const salKey = `${params.salaryMinEur ?? ""}:${params.salaryMaxEur ?? ""}`;
  const expKey = `${params.experienceMin ?? ""}:${params.experienceMax ?? ""}`;
  const key = `postings:${params.companyId}:${sortedKw.join(",")}:${sortedLoc.join(",")}:${sortedOcc.join(",")}:${sortedSen.join(",")}:${sortedTech}:${sortedLangs.join(",")}:${salKey}:${expKey}:${params.locale}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const [expandedLocs, expandedOccs] = await Promise.all([
        resolveLocationIds(params.locationIds),
        resolveOccupationIds(params.occupationIds),
      ]);
      return getSearchProvider().loadPostings({ ...params, locationIds: expandedLocs, occupationIds: expandedOccs });
    },
    { ttl: 300 },
  );
}
