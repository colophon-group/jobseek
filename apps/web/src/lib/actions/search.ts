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
  locations: { name: string; type: string }[];
  employmentType: string | null;
  sourceUrl: string;
  firstSeenAt: string;
  descriptionHtml: string | null;
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
  let locations: { name: string; type: string }[] = [];
  if (row.location_ids && row.location_ids.length > 0) {
    const pgArray = `{${row.location_ids.join(",")}}`;
    const locRows = await db.execute<{
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
    const nameMap = new Map<number, string>();
    for (const r of locRows as unknown as { location_id: number; name: string }[]) {
      nameMap.set(r.location_id, r.name);
    }
    locations = row.location_ids
      .map((id, i) => ({
        name: nameMap.get(id) ?? "",
        type: row.location_types?.[i] ?? "onsite",
      }))
      .filter((l) => l.name !== "");
  }

  // Fetch description from R2
  const r2Domain = process.env.R2_DOMAIN_URL;
  let descriptionHtml: string | null = null;
  if (r2Domain) {
    const descLocale = row.locales?.[0] ?? "en";
    const url = `${r2Domain.replace(/\/$/, "")}/job/${postingId}/${descLocale}/latest.html`;
    try {
      const resp = await fetch(url, { next: { revalidate: 300 } });
      if (resp.ok) {
        descriptionHtml = await resp.text();
      }
    } catch {
      // R2 unavailable — description stays null
    }
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
    descriptionHtml,
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
