"use server";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { getSearchProvider } from "@/lib/search";
import type { SearchResponse, SearchResultPosting } from "@/lib/search";
import { cached } from "@/lib/cache";
import { expandLocationIds } from "@/lib/actions/locations";

// ── Posting detail ──────────────────────────────────────────────────

export interface PostingDetail {
  id: string;
  title: string | null;
  company: { id: string; name: string; slug: string; logo: string | null; icon: string | null };
  locations: { name: string; type: string; geoType?: string }[];
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
  let locations: { name: string; type: string; geoType?: string }[] = [];
  if (row.location_ids && row.location_ids.length > 0) {
    const pgArray = `{${row.location_ids.join(",")}}`;
    const locRows = await db.execute<{
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
    const nameMap = new Map<number, { name: string; geoType: string }>();
    for (const r of locRows as unknown as { location_id: number; name: string; type: string }[]) {
      nameMap.set(r.location_id, { name: r.name, geoType: r.type });
    }
    locations = row.location_ids
      .map((id, i) => {
        const resolved = nameMap.get(id);
        return {
          name: resolved?.name ?? "",
          type: row.location_types?.[i] ?? "onsite",
          geoType: resolved?.geoType,
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

export async function searchJobs(params: {
  keywords: string[];
  locationIds?: number[];
  language: string;
  offset: number;
  limit: number;
}): Promise<SearchResponse> {
  const sortedKw = [...params.keywords].sort();
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const key = `search:${sortedKw.join(",")}:${sortedLoc.join(",")}:${params.language}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const expandedIds = await resolveLocationIds(params.locationIds);
      return getSearchProvider().search({ ...params, locationIds: expandedIds });
    },
    { ttl: 300 },
  );
}

export async function listTopCompanies(params: {
  locationIds?: number[];
  language: string;
  offset: number;
  limit: number;
}): Promise<SearchResponse> {
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const key = `top-companies:${sortedLoc.join(",")}:${params.language}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const expandedIds = await resolveLocationIds(params.locationIds);
      return getSearchProvider().listTopCompanies({ ...params, locationIds: expandedIds });
    },
    { ttl: 600 },
  );
}

export async function loadMorePostings(params: {
  companyId: string;
  keywords: string[];
  locationIds?: number[];
  language: string;
  offset: number;
  limit: number;
}): Promise<SearchResultPosting[]> {
  const sortedKw = [...params.keywords].sort();
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const key = `postings:${params.companyId}:${sortedKw.join(",")}:${sortedLoc.join(",")}:${params.language}:${params.offset}:${params.limit}`;
  return cached(
    key,
    async () => {
      const expandedIds = await resolveLocationIds(params.locationIds);
      return getSearchProvider().loadPostings({ ...params, locationIds: expandedIds });
    },
    { ttl: 300 },
  );
}
