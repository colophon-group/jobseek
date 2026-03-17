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
  locations: { name: string; type: string; geoType?: string; parentName?: string }[];
  employmentType: string | null;
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
      jp.locales
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    WHERE jp.id = ${postingId}
  `);

  type Row = {
    id: string; title: string | null;
    company_id: string; company_name: string; company_slug: string;
    company_logo: string | null; company_icon: string | null;
    location_ids: number[] | null; location_types: string[] | null;
    employment_type: string | null; source_url: string;
    first_seen_at: Date; locales: string[];
  };
  const row = (rows as unknown as Row[])[0];
  if (!row) return null;

  // Resolve location display names
  let locations: { name: string; type: string; geoType?: string; parentName?: string }[] = [];
  if (row.location_ids && row.location_ids.length > 0) {
    const pgArray = `{${row.location_ids.join(",")}}`;
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
    locations = row.location_ids
      .map((id, i) => {
        const resolved = nameMap.get(id);
        return {
          name: resolved?.name ?? "",
          type: row.location_types?.[i] ?? "onsite",
          geoType: resolved?.geoType,
          parentName: resolved?.parentName,
        };
      })
      .filter((l) => l.name !== "");
  }

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
  const sortedLangs = [...params.languages].sort();
  const salKey = `${params.salaryMinEur ?? ""}:${params.salaryMaxEur ?? ""}`;
  const expKey = `${params.experienceMax ?? ""}`;
  const key = `search:${sortedKw.join(",")}:${sortedLoc.join(",")}:${sortedOcc.join(",")}:${sortedSen.join(",")}:${sortedTech}:${sortedLangs.join(",")}:${salKey}:${expKey}:${params.locale}:${params.offset}:${params.limit}`;
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
  const sortedLangs = [...params.languages].sort();
  const salKey = `${params.salaryMinEur ?? ""}:${params.salaryMaxEur ?? ""}`;
  const expKey = `${params.experienceMax ?? ""}`;
  const key = `top-companies:${sortedLoc.join(",")}:${sortedOcc.join(",")}:${sortedSen.join(",")}:${sortedTech}:${sortedLangs.join(",")}:${salKey}:${expKey}:${params.locale}:${params.offset}:${params.limit}`;
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
  return cached(
    "currency-rates",
    async () => {
      try {
        const rows = await db.execute<{ [key: string]: unknown; currency: string; to_eur: string }>(
          sql`SELECT currency, to_eur FROM currency_rate ORDER BY currency`,
        );
        return (rows as unknown as { currency: string; to_eur: string }[]).map((r) => ({
          currency: r.currency,
          toEur: parseFloat(r.to_eur),
        }));
      } catch {
        // Table may not exist yet (migration not run)
        return [{ currency: "EUR", toEur: 1 }];
      }
    },
    { ttl: 3600 },
  );
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
