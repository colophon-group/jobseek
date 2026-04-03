"use server";

import { eq, and, sql } from "drizzle-orm";
import { db } from "@/db";
import {
  watchlist,
  watchlistCompany,
  company,
} from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { cached } from "@/lib/cache";
import { canCreateWatchlist, getUserPlan, PLAN_LIMITS } from "@/lib/plans";
import { generateUniqueSlug } from "@/lib/watchlist-slug";
import { ANON_MAX_WATCHLIST_POSTINGS } from "@/lib/search/constants";
import { expandLocationIds, resolveLocationSlugs } from "@/lib/actions/locations";
import { expandOccupationIds, resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/actions/taxonomy";
import { getSearchClient } from "@/lib/search/typesense-client";
import { buildFilterString } from "@/lib/search/typesense-filters";
import {
  upsertWatchlist as tsUpsertWatchlist,
  deleteWatchlist as tsDeleteWatchlist,
  updateWatchlistField as tsUpdateWatchlistField,
} from "@/lib/search/typesense-watchlist";

// ── Types ───────────────────────────────────────────────────────────

export type WatchlistFilters = {
  keywords?: string[];
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  anyCompany?: boolean;
};

export type WatchlistSummary = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  isPublic: boolean;
  alertsEnabled: boolean;
  companyCount: number;
  activeJobCount: number;
  lastAccessedAt: string;
  createdAt: string;
};

export type WatchlistDetail = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  isPublic: boolean;
  alertsEnabled: boolean;
  filters: WatchlistFilters;
  sourceWatchlistId: string | null;
  createdAt: string;
  owner: {
    id: string;
    username: string | null;
    displayUsername: string | null;
    name: string;
  };
  companies: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  }[];
};

export type WatchlistPostingEntry = {
  id: string;
  title: string | null;
  sourceUrl: string;
  firstSeenAt: string;
  isActive: boolean;
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
};

// ── Actions ─────────────────────────────────────────────────────────

export async function createWatchlist(params: {
  title: string;
  description?: string;
  companyIds: string[];
  filters?: WatchlistFilters;
  isPublic?: boolean;
}): Promise<{ id: string; slug: string } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const { allowed } = await canCreateWatchlist(userId);
  if (!allowed) return { error: "limit_reached" };

  const slug = await generateUniqueSlug(userId, params.title);

  const [row] = await db
    .insert(watchlist)
    .values({
      userId,
      slug,
      title: params.title,
      description: params.description ?? null,
      isPublic: params.isPublic ?? true,
      filters: { anyCompany: true, ...params.filters },
    })
    .returning({ id: watchlist.id });

  if (params.companyIds.length > 0) {
    await db.insert(watchlistCompany).values(
      params.companyIds.map((companyId) => ({
        watchlistId: row.id,
        companyId,
      })),
    );
  }

  // Typesense write hook: upsert if public (fire-and-forget)
  const isPublic = params.isPublic ?? true;
  if (isPublic) {
    // Fetch owner info for the Typesense doc
    _getOwnerInfo(userId).then((owner) => {
      if (!owner) return;
      tsUpsertWatchlist({
        id: row.id,
        slug,
        title: params.title,
        description: params.description,
        owner_name: owner.name,
        owner_username: owner.username ?? undefined,
        company_count: params.companyIds.length,
        active_job_count: 0, // will be refreshed by reconciliation cron
        mirror_count: 0,
        created_at: Math.floor(Date.now() / 1000),
        is_public: true,
      });
    }).catch((err) => {
      console.error("[createWatchlist] Typesense hook failed", err);
    });
  }

  return { id: row.id, slug };
}

export async function updateWatchlist(params: {
  watchlistId: string;
  title?: string;
  description?: string | null;
  companyIds?: string[];
  filters?: WatchlistFilters;
  isPublic?: boolean;
}): Promise<{ slug: string } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({
      id: watchlist.id,
      userId: watchlist.userId,
      slug: watchlist.slug,
      title: watchlist.title,
      description: watchlist.description,
      isPublic: watchlist.isPublic,
    })
    .from(watchlist)
    .where(eq(watchlist.id, params.watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { error: "not_found" };

  let newSlug = wl.slug;
  const updates: Record<string, unknown> = {};

  if (params.title !== undefined) {
    updates.title = params.title;
    newSlug = await generateUniqueSlug(userId, params.title);
    updates.slug = newSlug;
  }
  if (params.description !== undefined) updates.description = params.description;
  if (params.filters !== undefined) updates.filters = params.filters;
  if (params.isPublic !== undefined) updates.isPublic = params.isPublic;

  if (Object.keys(updates).length > 0) {
    await db
      .update(watchlist)
      .set(updates)
      .where(eq(watchlist.id, params.watchlistId));
  }

  if (params.companyIds !== undefined) {
    await db
      .delete(watchlistCompany)
      .where(eq(watchlistCompany.watchlistId, params.watchlistId));

    if (params.companyIds.length > 0) {
      await db.insert(watchlistCompany).values(
        params.companyIds.map((companyId) => ({
          watchlistId: params.watchlistId,
          companyId,
        })),
      );
    }
  }

  // Typesense write hook (fire-and-forget)
  const wasPublic = wl.isPublic;
  const nowPublic = params.isPublic !== undefined ? params.isPublic : wasPublic;

  if (nowPublic) {
    // Upsert: the watchlist is (still or newly) public
    // Use partial update for fields we know; reconciliation cron handles the rest.
    const tsFields: Record<string, unknown> = {
      slug: newSlug,
      title: params.title ?? wl.title,
      is_public: true,
    };
    if (params.description !== undefined) {
      tsFields.description = params.description ?? "";
    }
    if (params.companyIds !== undefined) {
      tsFields.company_count = params.companyIds.length;
    }
    tsUpdateWatchlistField(params.watchlistId, tsFields);

    // If transitioning from private to public, the doc may not exist yet — do a full upsert
    if (!wasPublic) {
      _getOwnerInfo(userId).then(async (owner) => {
        if (!owner) return;
        const companyCount = params.companyIds !== undefined
          ? params.companyIds.length
          : await _countWatchlistCompanies(params.watchlistId);
        tsUpsertWatchlist({
          id: params.watchlistId,
          slug: newSlug,
          title: params.title ?? wl.title,
          description: (params.description !== undefined ? params.description : wl.description) ?? undefined,
          owner_name: owner.name,
          owner_username: owner.username ?? undefined,
          company_count: companyCount,
          active_job_count: 0, // refreshed by reconciliation cron
          mirror_count: 0,
          created_at: Math.floor(Date.now() / 1000),
          is_public: true,
        });
      }).catch((err) => {
        console.error("[updateWatchlist] Typesense hook failed", err);
      });
    }
  } else if (wasPublic && !nowPublic) {
    // Was public, now private — delete from Typesense
    tsDeleteWatchlist(params.watchlistId);
  }

  return { slug: newSlug };
}

export async function deleteWatchlist(
  watchlistId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db.delete(watchlist).where(eq(watchlist.id, watchlistId));

  // Typesense write hook: delete (fire-and-forget, safe even if doc doesn't exist)
  tsDeleteWatchlist(watchlistId);

  return { ok: true };
}

export async function copyWatchlist(
  watchlistId: string,
): Promise<{ id: string; slug: string } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const { allowed } = await canCreateWatchlist(userId);
  if (!allowed) return { error: "limit_reached" };

  const [source] = await db
    .select({
      title: watchlist.title,
      description: watchlist.description,
      filters: watchlist.filters,
      isPublic: watchlist.isPublic,
      userId: watchlist.userId,
    })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  // Allow mirroring if public or owned by the user
  if (!source || (!source.isPublic && source.userId !== userId)) {
    return { error: "not_found" };
  }

  const slug = await generateUniqueSlug(userId, source.title);

  const sourceFilters = (source.filters ?? {}) as WatchlistFilters;

  const [row] = await db
    .insert(watchlist)
    .values({
      userId,
      slug,
      title: source.title,
      description: source.description,
      isPublic: true,
      filters: sourceFilters,
      sourceWatchlistId: watchlistId,
    })
    .returning({ id: watchlist.id });

  // Copy companies (even in anyCompany mode, so toggling it off reveals them)
  const companies = await db
    .select({ companyId: watchlistCompany.companyId })
    .from(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));

  if (companies.length > 0) {
    await db.insert(watchlistCompany).values(
      companies.map((c) => ({
        watchlistId: row.id,
        companyId: c.companyId,
      })),
    );
  }

  // Typesense write hooks (fire-and-forget):
  // 1. Upsert the new copy (copies are always public)
  _getOwnerInfo(userId).then((owner) => {
    if (!owner) return;
    tsUpsertWatchlist({
      id: row.id,
      slug,
      title: source.title,
      description: source.description ?? undefined,
      owner_name: owner.name,
      owner_username: owner.username ?? undefined,
      company_count: companies.length,
      active_job_count: 0, // refreshed by reconciliation cron
      mirror_count: 0,
      created_at: Math.floor(Date.now() / 1000),
      is_public: true,
    });
  }).catch((err) => {
    console.error("[copyWatchlist] Typesense upsert hook failed", err);
  });

  // 2. Update source watchlist's mirror_count (increment)
  _getWatchlistMirrorCount(watchlistId).then((count) => {
    tsUpdateWatchlistField(watchlistId, { mirror_count: count });
  }).catch((err) => {
    console.error("[copyWatchlist] Typesense mirror_count hook failed", err);
  });

  return { id: row.id, slug };
}

export async function toggleWatchlistAlerts(
  watchlistId: string,
): Promise<{ enabled: boolean } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({
      userId: watchlist.userId,
      alertsEnabled: watchlist.alertsEnabled,
    })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { error: "not_found" };

  const plan = await getUserPlan(userId);
  if (!PLAN_LIMITS[plan].canReceiveAlerts) return { error: "paid_only" };

  const newVal = !wl.alertsEnabled;
  await db
    .update(watchlist)
    .set({ alertsEnabled: newVal })
    .where(eq(watchlist.id, watchlistId));

  return { enabled: newVal };
}

export async function getUserWatchlists(): Promise<WatchlistSummary[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const rows = await db.execute<{
    [key: string]: unknown;
    id: string;
    slug: string;
    title: string;
    is_public: boolean;
    alerts_enabled: boolean;
    filters: WatchlistFilters;
    last_accessed_at: Date;
    created_at: Date;
    company_count: number;
    company_ids: string[];
  }>(sql`
    SELECT w.id, w.slug, w.title, w.description, w.is_public, w.alerts_enabled, w.filters,
           w.last_accessed_at, w.created_at,
           (SELECT count(*)::int FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_count,
           (SELECT coalesce(array_agg(wc.company_id), '{}') FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_ids
    FROM watchlist w
    WHERE w.user_id = ${userId}
    ORDER BY w.last_accessed_at DESC
  `);

  type Row = {
    id: string; slug: string; title: string; description: string | null; is_public: boolean;
    alerts_enabled: boolean; filters: WatchlistFilters; last_accessed_at: Date; created_at: Date;
    company_count: number; company_ids: string[];
  };

  const typed = rows as unknown as Row[];

  // Compute active job counts respecting each watchlist's filters (cached 5min)
  const counts = await Promise.all(
    typed.map((r) => resolveFilteredJobCount(r.id, r.filters ?? {}, r.company_ids ?? [])),
  );

  return typed.map((r, i) => ({
    id: r.id,
    slug: r.slug,
    title: r.title,
    description: r.description,
    isPublic: r.is_public,
    alertsEnabled: r.alerts_enabled,
    companyCount: r.company_count,
    activeJobCount: counts[i],
    lastAccessedAt: new Date(r.last_accessed_at).toISOString(),
    createdAt: new Date(r.created_at).toISOString(),
  }));
}

export async function getWatchlistByUserAndSlug(
  userSlug: string,
  watchlistSlug: string,
): Promise<WatchlistDetail | null> {
  const sessionUserId = await getSessionUserId();

  // Resolve user + watchlist in a single JOIN
  type WatchlistJoinRow = {
    wl_id: string; slug: string; title: string; description: string | null;
    is_public: boolean; alerts_enabled: boolean; filters: WatchlistFilters | null;
    source_watchlist_id: string | null; created_at: Date; user_id: string;
    owner_id: string; username: string | null;
    display_username: string | null; owner_name: string;
  };

  const rows = await db.execute<{ [key: string]: unknown } & WatchlistJoinRow>(sql`
    SELECT
      w.id AS wl_id, w.slug, w.title, w.description,
      w.is_public, w.alerts_enabled, w.filters,
      w.source_watchlist_id, w.created_at, w.user_id,
      u.id AS owner_id, u.username, u.display_username, u.name AS owner_name
    FROM watchlist w
    JOIN "user" u ON u.id = w.user_id
    WHERE u.username = ${userSlug} AND w.slug = ${watchlistSlug}
    LIMIT 1
  `);

  const row = (rows as unknown as WatchlistJoinRow[])[0];
  if (!row) return null;

  // Access check: public or owner
  if (!row.is_public && row.user_id !== sessionUserId) return null;

  // Touch lastAccessedAt (fire-and-forget — doesn't affect response)
  if (row.user_id === sessionUserId) {
    db.update(watchlist)
      .set({ lastAccessedAt: new Date() })
      .where(eq(watchlist.id, row.wl_id))
      .catch(() => {});
  }

  // Fetch companies
  const companies = await db
    .select({
      id: company.id,
      name: company.name,
      slug: company.slug,
      icon: company.icon,
    })
    .from(watchlistCompany)
    .innerJoin(company, eq(watchlistCompany.companyId, company.id))
    .where(eq(watchlistCompany.watchlistId, row.wl_id))
    .orderBy(company.name);

  return {
    id: row.wl_id,
    slug: row.slug,
    title: row.title,
    description: row.description,
    isPublic: row.is_public,
    alertsEnabled: row.alerts_enabled,
    filters: (row.filters ?? {}) as WatchlistFilters,
    sourceWatchlistId: row.source_watchlist_id,
    createdAt: new Date(row.created_at).toISOString(),
    owner: {
      id: row.owner_id,
      username: row.username,
      displayUsername: row.display_username,
      name: row.owner_name,
    },
    companies,
  };
}

export type PublicWatchlistEntry = WatchlistSummary & {
  ownerName: string;
  ownerUsername: string | null;
  mirrorCount: number;
};

function buildFilterCacheKey(f: WatchlistFilters, companyIds: string[]): string {
  const parts: string[] = [];
  if (f.anyCompany) parts.push("any");
  if (companyIds.length) parts.push(`c:${[...companyIds].sort().join(",")}`);
  if (f.keywords?.length) parts.push(`kw:${[...f.keywords].sort().join(",")}`);
  if (f.locationSlugs?.length) parts.push(`loc:${[...f.locationSlugs].sort().join(",")}`);
  if (f.occupationSlugs?.length) parts.push(`occ:${[...f.occupationSlugs].sort().join(",")}`);
  if (f.senioritySlugs?.length) parts.push(`sen:${[...f.senioritySlugs].sort().join(",")}`);
  if (f.technologySlugs?.length) parts.push(`tech:${[...f.technologySlugs].sort().join(",")}`);
  if (f.salaryMin != null) parts.push(`smin:${f.salaryMin}`);
  if (f.salaryMax != null) parts.push(`smax:${f.salaryMax}`);
  if (f.experienceMin != null) parts.push(`emin:${f.experienceMin}`);
  if (f.experienceMax != null) parts.push(`emax:${f.experienceMax}`);
  return parts.join("|");
}

async function resolveFilteredJobCount(
  watchlistId: string,
  f: WatchlistFilters,
  companyIds: string[],
): Promise<number> {
  const isAny = f.anyCompany;
  if (!isAny && companyIds.length === 0) return 0;

  const key = `wl-count:${watchlistId}:${buildFilterCacheKey(f, companyIds)}`;
  return cached(key, async () => {
    const locale = "en";
    const [locMap, occMap, senMap, techMap] = await Promise.all([
      f.locationSlugs?.length ? resolveLocationSlugs(f.locationSlugs, locale) : Promise.resolve(new Map()),
      f.occupationSlugs?.length ? resolveOccupationSlugs(f.occupationSlugs, locale) : Promise.resolve(new Map()),
      f.senioritySlugs?.length ? resolveSenioritySlugs(f.senioritySlugs, locale) : Promise.resolve(new Map()),
      f.technologySlugs?.length ? resolveTechnologySlugs(f.technologySlugs) : Promise.resolve(new Map()),
    ]);

    const { total } = await getWatchlistPostings({
      companyIds: isAny ? [] : companyIds,
      anyCompany: isAny,
      offset: 0,
      limit: 0,
      keywords: f.keywords,
      locationIds: locMap.size > 0 ? [...locMap.values()].map((l) => l.id) : undefined,
      occupationIds: occMap.size > 0 ? [...occMap.values()].map((o) => o.id) : undefined,
      seniorityIds: senMap.size > 0 ? [...senMap.values()].map((s) => s.id) : undefined,
      technologyIds: techMap.size > 0 ? [...techMap.values()].map((t) => t.id) : undefined,
      salaryMin: f.salaryMin,
      salaryMax: f.salaryMax,
      experienceMin: f.experienceMin,
      experienceMax: f.experienceMax,
    });
    return total;
  }, { ttl: 300 });
}

async function queryPublicWatchlists(params: {
  whereClause: ReturnType<typeof sql>;
  orderClause: ReturnType<typeof sql>;
  offset: number;
  limit: number;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const [totalRow] = await db.execute<{ [key: string]: unknown; cnt: number }>(sql`
    SELECT count(*)::int AS cnt FROM watchlist w WHERE ${params.whereClause}
  `);
  const total = (totalRow as unknown as { cnt: number })?.cnt ?? 0;
  if (total === 0) return { watchlists: [], total: 0 };

  const rows = await db.execute<{
    [key: string]: unknown;
    id: string; slug: string; title: string; is_public: boolean;
    alerts_enabled: boolean; filters: WatchlistFilters;
    last_accessed_at: Date; created_at: Date;
    owner_name: string; owner_username: string | null;
    company_count: number; company_ids: string[];
    mirror_count: number;
  }>(sql`
    SELECT w.id, w.slug, w.title, w.description, w.is_public, w.alerts_enabled, w.filters,
           w.last_accessed_at, w.created_at,
           u.name AS owner_name, u.username AS owner_username,
           (SELECT count(*)::int FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_count,
           (SELECT coalesce(array_agg(wc.company_id), '{}') FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_ids,
           (SELECT count(*)::int FROM watchlist w2 WHERE w2.source_watchlist_id = w.id) AS mirror_count
    FROM watchlist w
    JOIN "user" u ON u.id = w.user_id
    WHERE ${params.whereClause}
    ORDER BY ${params.orderClause}
    OFFSET ${params.offset}
    LIMIT ${params.limit}
  `);

  type Row = {
    id: string; slug: string; title: string; description: string | null; is_public: boolean;
    alerts_enabled: boolean; filters: WatchlistFilters;
    last_accessed_at: Date; created_at: Date;
    owner_name: string; owner_username: string | null;
    company_count: number; company_ids: string[];
    mirror_count: number;
  };

  const typed = rows as unknown as Row[];

  // Compute filtered job counts in parallel (cached 5min)
  const counts = await Promise.all(
    typed.map((r) => resolveFilteredJobCount(r.id, r.filters ?? {}, r.company_ids ?? [])),
  );

  return {
    watchlists: typed.map((r, i) => ({
      id: r.id,
      slug: r.slug,
      title: r.title,
      description: r.description,
      isPublic: r.is_public,
      alertsEnabled: r.alerts_enabled,
      companyCount: r.company_count,
      activeJobCount: counts[i],
      lastAccessedAt: new Date(r.last_accessed_at).toISOString(),
      createdAt: new Date(r.created_at).toISOString(),
      ownerName: r.owner_name,
      ownerUsername: r.owner_username,
      mirrorCount: r.mirror_count,
    })),
    total,
  };
}

export async function searchPublicWatchlists(params: {
  query: string;
  offset: number;
  limit: number;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const q = params.query.trim();
  if (!q) return { watchlists: [], total: 0 };

  try {
    const tsResult = await _searchPublicWatchlistsTypesense(q, params.offset, params.limit);
    // Enrich with real job counts from Postgres (Typesense active_job_count may be stale)
    if (tsResult.watchlists.length > 0) {
      return { watchlists: await _enrichWatchlistsWithRealCounts(tsResult.watchlists), total: tsResult.total };
    }
    return tsResult;
  } catch (err) {
    console.error("[searchPublicWatchlists] Typesense failed, falling back to Postgres", err);
    return queryPublicWatchlists({
      whereClause: sql`w.is_public = true AND (w.title ILIKE ${"%" + q + "%"} OR w.description ILIKE ${"%" + q + "%"})`,
      orderClause: sql`w.created_at DESC`,
      offset: params.offset,
      limit: params.limit,
    });
  }
}

export async function getPopularWatchlists(params: {
  offset: number;
  limit: number;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  try {
    const tsResult = await _getPopularWatchlistsTypesense(params.offset, params.limit);
    // Enrich with real job counts from Postgres (Typesense active_job_count may be stale)
    if (tsResult.watchlists.length > 0) {
      return { watchlists: await _enrichWatchlistsWithRealCounts(tsResult.watchlists), total: tsResult.total };
    }
    return tsResult;
  } catch (err) {
    console.error("[getPopularWatchlists] Typesense failed, falling back to Postgres", err);
    return queryPublicWatchlists({
      whereClause: sql`w.is_public = true`,
      orderClause: sql`(SELECT count(*)::int FROM watchlist w2 WHERE w2.source_watchlist_id = w.id) DESC, w.created_at DESC`,
      offset: params.offset,
      limit: params.limit,
    });
  }
}

export async function getWatchlistPostings(params: {
  companyIds: string[];
  anyCompany?: boolean;
  offset: number;
  limit: number;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
}): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  // No companies selected and not "any company" mode → empty
  if (!params.anyCompany && params.companyIds.length === 0) {
    return { postings: [], total: 0 };
  }

  // Enforce truncation for unauthenticated users
  const userId = await getSessionUserId();
  if (!userId && params.offset >= ANON_MAX_WATCHLIST_POSTINGS) {
    return { postings: [], total: 0, truncated: true };
  }

  try {
    return await _getWatchlistPostingsTypesense(params, userId);
  } catch (err) {
    console.error("[getWatchlistPostings] Typesense failed, falling back to Postgres", err);
    return _getWatchlistPostingsPostgres(params, userId);
  }
}

export async function addCompanyToWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .insert(watchlistCompany)
    .values({ watchlistId, companyId })
    .onConflictDoNothing();

  // Typesense write hook: update company_count if public (fire-and-forget)
  if (wl.isPublic) {
    _countWatchlistCompanies(watchlistId).then((count) => {
      tsUpdateWatchlistField(watchlistId, { company_count: count });
    }).catch((err) => {
      console.error("[addCompanyToWatchlist] Typesense hook failed", err);
    });
  }

  return { ok: true };
}

export async function clearWatchlistCompanies(
  watchlistId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .delete(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));

  // Typesense write hook: set company_count to 0 if public (fire-and-forget)
  if (wl.isPublic) {
    tsUpdateWatchlistField(watchlistId, { company_count: 0 });
  }

  return { ok: true };
}

export async function removeCompanyFromWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .delete(watchlistCompany)
    .where(
      and(
        eq(watchlistCompany.watchlistId, watchlistId),
        eq(watchlistCompany.companyId, companyId),
      ),
    );

  // Typesense write hook: update company_count if public (fire-and-forget)
  if (wl.isPublic) {
    _countWatchlistCompanies(watchlistId).then((count) => {
      tsUpdateWatchlistField(watchlistId, { company_count: count });
    }).catch((err) => {
      console.error("[removeCompanyFromWatchlist] Typesense hook failed", err);
    });
  }

  return { ok: true };
}

// ── Typesense search implementations ──────────────────────────────────

async function _searchPublicWatchlistsTypesense(
  query: string,
  offset: number,
  limit: number,
): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const client = getSearchClient();

  const result = await client.collections("watchlist").documents().search({
    q: query,
    query_by: "title,description",
    filter_by: "is_public:true",
    sort_by: "_text_match:desc,created_at:desc",
    per_page: limit,
    page: Math.floor(offset / limit) + 1,
    prefix: true,
    num_typos: 1,
  });

  return {
    watchlists: (result.hits ?? []).map((hit) => {
      const doc = hit.document as Record<string, unknown>;
      return _mapWatchlistDoc(doc);
    }),
    total: result.found ?? 0,
  };
}

async function _getPopularWatchlistsTypesense(
  offset: number,
  limit: number,
): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const client = getSearchClient();

  const result = await client.collections("watchlist").documents().search({
    q: "*",
    query_by: "title,description",
    filter_by: "is_public:true",
    sort_by: "mirror_count:desc,created_at:desc",
    per_page: limit,
    page: Math.floor(offset / limit) + 1,
  });

  return {
    watchlists: (result.hits ?? []).map((hit) => {
      const doc = hit.document as Record<string, unknown>;
      return _mapWatchlistDoc(doc);
    }),
    total: result.found ?? 0,
  };
}

function _mapWatchlistDoc(doc: Record<string, unknown>): PublicWatchlistEntry {
  const createdAtTs = doc.created_at as number;
  return {
    id: doc.id as string,
    slug: doc.slug as string,
    title: doc.title as string,
    description: (doc.description as string) ?? null,
    isPublic: true,
    alertsEnabled: false, // not stored in Typesense; display-only field
    companyCount: (doc.company_count as number) ?? 0,
    activeJobCount: (doc.active_job_count as number) ?? 0,
    lastAccessedAt: new Date(createdAtTs * 1000).toISOString(), // approximate
    createdAt: new Date(createdAtTs * 1000).toISOString(),
    ownerName: (doc.owner_name as string) ?? "",
    ownerUsername: (doc.owner_username as string) ?? null,
    mirrorCount: (doc.mirror_count as number) ?? 0,
  };
}

/**
 * Enrich Typesense-sourced watchlist entries with real activeJobCount
 * computed from Postgres (the Typesense watchlist collection may have stale counts).
 */
async function _enrichWatchlistsWithRealCounts(
  watchlists: PublicWatchlistEntry[],
): Promise<PublicWatchlistEntry[]> {
  const ids = watchlists.map((w) => w.id);
  if (ids.length === 0) return watchlists;

  const pgArr = `{${ids.join(",")}}`;
  const rows = await db.execute<{
    [key: string]: unknown;
    id: string;
    filters: WatchlistFilters | null;
    company_ids: string[];
  }>(sql`
    SELECT w.id, w.filters,
           (SELECT coalesce(array_agg(wc.company_id), '{}') FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_ids
    FROM watchlist w
    WHERE w.id = ANY(${pgArr}::uuid[])
  `);

  type Row = { id: string; filters: WatchlistFilters | null; company_ids: string[] };
  const rowMap = new Map<string, Row>();
  for (const r of rows as unknown as Row[]) {
    rowMap.set(r.id, r);
  }

  const counts = await Promise.all(
    watchlists.map((w) => {
      const pgRow = rowMap.get(w.id);
      if (!pgRow) return Promise.resolve(0);
      return resolveFilteredJobCount(w.id, pgRow.filters ?? {}, pgRow.company_ids ?? []);
    }),
  );

  return watchlists.map((w, i) => ({
    ...w,
    activeJobCount: counts[i],
  }));
}

/** Max company IDs per Typesense filter string batch (~7KB ≈ 200 UUIDs). */
const COMPANY_BATCH_SIZE = 100;

async function _getWatchlistPostingsTypesense(
  params: {
    companyIds: string[];
    anyCompany?: boolean;
    offset: number;
    limit: number;
    keywords?: string[];
    locationIds?: number[];
    occupationIds?: number[];
    seniorityIds?: number[];
    technologyIds?: number[];
    salaryMin?: number;
    salaryMax?: number;
    experienceMin?: number;
    experienceMax?: number;
  },
  userId: string | null,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  const client = getSearchClient();

  // No expansion needed — ancestor IDs are stored on each Typesense document
  // Build filter string from watchlist context filters
  // Map salaryMin/salaryMax to salaryMinEur/salaryMaxEur
  const filterStr = buildFilterString({
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
  });

  const hasKeywords = params.keywords && params.keywords.length > 0;
  const keywordsQ = hasKeywords ? params.keywords!.join(" ") : "*";

  // Build company_id filter — omit for "any company" mode
  let companyFilter = "";
  if (params.companyIds.length > 0) {
    if (params.companyIds.length > COMPANY_BATCH_SIZE) {
      // Large watchlist: batch queries and merge
      return _getWatchlistPostingsBatched(params, userId);
    }
    companyFilter = `company_id:[${params.companyIds.join(",")}]`;
  }

  // Combine all filter parts
  const filterParts = ["is_active:true"];
  if (companyFilter) filterParts.push(companyFilter);
  if (filterStr) filterParts.push(filterStr);
  const fullFilter = filterParts.join(" && ");

  const result = await client.collections("job_posting").documents().search({
    q: keywordsQ,
    query_by: "title",
    filter_by: fullFilter,
    sort_by: hasKeywords ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
    per_page: params.limit === 0 ? 0 : params.limit,
    page: params.limit === 0 ? 1 : Math.floor(params.offset / params.limit) + 1,
  });

  const total = result.found ?? 0;
  if (total === 0 || params.limit === 0) return { postings: [], total };

  const postings: WatchlistPostingEntry[] = (result.hits ?? []).map((hit) => {
    const doc = hit.document as Record<string, unknown>;
    return {
      id: doc.id as string,
      title: (doc.title as string) ?? null,
      sourceUrl: (doc.source_url as string) ?? "",
      firstSeenAt: new Date(((doc.first_seen_at as number) ?? 0) * 1000).toISOString(),
      isActive: (doc.is_active as boolean) ?? true,
      company: {
        id: (doc.company_id as string) ?? "",
        name: (doc.company_name as string) ?? "",
        slug: (doc.company_slug as string) ?? "",
        icon: (doc.company_icon as string) ?? null,
      },
    };
  });

  return {
    postings,
    total,
    ...(!userId && params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS ? { truncated: true } : {}),
  };
}

/** Batched version for large watchlists (200+ companies). */
async function _getWatchlistPostingsBatched(
  params: {
    companyIds: string[];
    anyCompany?: boolean;
    offset: number;
    limit: number;
    keywords?: string[];
    locationIds?: number[];
    occupationIds?: number[];
    seniorityIds?: number[];
    technologyIds?: number[];
    salaryMin?: number;
    salaryMax?: number;
    experienceMin?: number;
    experienceMax?: number;
  },
  userId: string | null,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  const client = getSearchClient();

  // No expansion needed — ancestor IDs are stored on each Typesense document
  const filterStr = buildFilterString({
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
  });

  const hasKeywords = params.keywords && params.keywords.length > 0;
  const keywordsQ = hasKeywords ? params.keywords!.join(" ") : "*";

  // Split company IDs into batches
  const batches: string[][] = [];
  for (let i = 0; i < params.companyIds.length; i += COMPANY_BATCH_SIZE) {
    batches.push(params.companyIds.slice(i, i + COMPANY_BATCH_SIZE));
  }

  // Query each batch for total count (per_page: 0)
  const countResults = await Promise.all(
    batches.map((batch) => {
      const filterParts = ["is_active:true", `company_id:[${batch.join(",")}]`];
      if (filterStr) filterParts.push(filterStr);
      return client.collections("job_posting").documents().search({
        q: keywordsQ,
        query_by: "title",
        filter_by: filterParts.join(" && "),
        per_page: 0,
      });
    }),
  );

  const total = countResults.reduce((sum, r) => sum + (r.found ?? 0), 0);
  if (total === 0 || params.limit === 0) return { postings: [], total };

  // For actual postings, query all batches with enough per_page to cover offset+limit,
  // then merge and sort by first_seen_at desc, slice to desired page.
  const needed = params.offset + params.limit;
  const postingsResults = await Promise.all(
    batches.map((batch) => {
      const filterParts = ["is_active:true", `company_id:[${batch.join(",")}]`];
      if (filterStr) filterParts.push(filterStr);
      return client.collections("job_posting").documents().search({
        q: keywordsQ,
        query_by: "title",
        filter_by: filterParts.join(" && "),
        sort_by: hasKeywords ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
        per_page: needed,
        page: 1,
      });
    }),
  );

  // Merge all hits, sort, and paginate
  const allHits = postingsResults.flatMap((r) => r.hits ?? []);
  allHits.sort((a, b) => {
    const aDoc = a.document as Record<string, unknown>;
    const bDoc = b.document as Record<string, unknown>;
    return ((bDoc.first_seen_at as number) ?? 0) - ((aDoc.first_seen_at as number) ?? 0);
  });

  const pageHits = allHits.slice(params.offset, params.offset + params.limit);

  const postings: WatchlistPostingEntry[] = pageHits.map((hit) => {
    const doc = hit.document as Record<string, unknown>;
    return {
      id: doc.id as string,
      title: (doc.title as string) ?? null,
      sourceUrl: (doc.source_url as string) ?? "",
      firstSeenAt: new Date(((doc.first_seen_at as number) ?? 0) * 1000).toISOString(),
      isActive: (doc.is_active as boolean) ?? true,
      company: {
        id: (doc.company_id as string) ?? "",
        name: (doc.company_name as string) ?? "",
        slug: (doc.company_slug as string) ?? "",
        icon: (doc.company_icon as string) ?? null,
      },
    };
  });

  return {
    postings,
    total,
    ...(!userId && params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS ? { truncated: true } : {}),
  };
}

/** Postgres fallback for getWatchlistPostings (graceful degradation). */
async function _getWatchlistPostingsPostgres(
  params: {
    companyIds: string[];
    anyCompany?: boolean;
    offset: number;
    limit: number;
    keywords?: string[];
    locationIds?: number[];
    occupationIds?: number[];
    seniorityIds?: number[];
    technologyIds?: number[];
    salaryMin?: number;
    salaryMax?: number;
    experienceMin?: number;
    experienceMax?: number;
  },
  userId: string | null,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  // Expand parent locations/occupations to include children
  const [expandedLocationIds, expandedOccupationIds] = await Promise.all([
    params.locationIds && params.locationIds.length > 0
      ? Promise.all(params.locationIds.map(expandLocationIds)).then((a) => [...new Set(a.flat())])
      : undefined,
    params.occupationIds && params.occupationIds.length > 0
      ? Promise.all(params.occupationIds.map(expandOccupationIds)).then((a) => [...new Set(a.flat())])
      : undefined,
  ]);

  const clauses = [sql`jp.is_active = true`];

  if (params.companyIds.length > 0) {
    const pgCompanyArray = `{${params.companyIds.join(",")}}`;
    clauses.push(sql`jp.company_id = ANY(${pgCompanyArray}::uuid[])`);
  }

  if (params.keywords && params.keywords.length > 0) {
    const kwClauses = params.keywords.map((k) => {
      const escaped = k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const startBound = /^\w/.test(k) ? "\\m" : "";
      const endBound = /\w$/.test(k) ? "\\M" : "";
      return sql`jp.titles[1] ~* ${startBound + escaped + endBound}`;
    });
    clauses.push(sql`(${sql.join(kwClauses, sql` OR `)})`);
  }
  if (expandedLocationIds && expandedLocationIds.length > 0) {
    const pgArr = `{${expandedLocationIds.join(",")}}`;
    clauses.push(sql`jp.location_ids && ${pgArr}::integer[]`);
  }
  if (expandedOccupationIds && expandedOccupationIds.length > 0) {
    const pgArr = `{${expandedOccupationIds.join(",")}}`;
    clauses.push(sql`jp.occupation_id = ANY(${pgArr}::integer[])`);
  }
  if (params.seniorityIds && params.seniorityIds.length > 0) {
    const pgArr = `{${params.seniorityIds.join(",")}}`;
    clauses.push(sql`jp.seniority_id = ANY(${pgArr}::integer[])`);
  }
  if (params.technologyIds && params.technologyIds.length > 0) {
    const pgArr = `{${params.technologyIds.join(",")}}`;
    clauses.push(sql`jp.technology_ids && ${pgArr}::integer[]`);
  }
  if (params.salaryMin != null && params.salaryMax != null) {
    clauses.push(sql`jp.salary_eur BETWEEN ${params.salaryMin} AND ${params.salaryMax}`);
  } else if (params.salaryMin != null) {
    clauses.push(sql`jp.salary_eur >= ${params.salaryMin}`);
  } else if (params.salaryMax != null) {
    clauses.push(sql`jp.salary_eur <= ${params.salaryMax}`);
  }
  if (params.experienceMin != null || params.experienceMax != null) {
    if (params.experienceMin != null && params.experienceMax != null) {
      clauses.push(sql`(jp.experience_min IS NULL OR (jp.experience_min >= ${params.experienceMin} AND jp.experience_min <= ${params.experienceMax}))`);
    } else if (params.experienceMin != null) {
      clauses.push(sql`(jp.experience_min IS NULL OR jp.experience_min >= ${params.experienceMin})`);
    } else {
      clauses.push(sql`(jp.experience_min IS NULL OR jp.experience_min <= ${params.experienceMax!})`);
    }
  }

  const whereClause = sql.join(clauses, sql` AND `);

  const [totalRow] = await db.execute<{ [key: string]: unknown; cnt: number }>(
    sql`SELECT count(*)::int AS cnt FROM job_posting jp WHERE ${whereClause}`,
  );
  const total = (totalRow as unknown as { cnt: number })?.cnt ?? 0;
  if (total === 0 || params.limit === 0) return { postings: [], total };

  const rows = await db.execute<{
    [key: string]: unknown;
    id: string;
    title: string | null;
    source_url: string;
    first_seen_at: Date;
    is_active: boolean;
    company_id: string;
    company_name: string;
    company_slug: string;
    company_icon: string | null;
  }>(sql`
    SELECT jp.id, jp.titles[1] AS title, jp.source_url, jp.first_seen_at, jp.is_active,
           c.id AS company_id, c.name AS company_name, c.slug AS company_slug, c.icon AS company_icon
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    WHERE ${whereClause}
    ORDER BY jp.first_seen_at DESC
    OFFSET ${params.offset}
    LIMIT ${params.limit}
  `);

  type Row = {
    id: string; title: string | null; source_url: string; first_seen_at: Date;
    is_active: boolean; company_id: string; company_name: string;
    company_slug: string; company_icon: string | null;
  };

  return {
    postings: (rows as unknown as Row[]).map((r) => ({
      id: r.id,
      title: r.title,
      sourceUrl: r.source_url,
      firstSeenAt: new Date(r.first_seen_at).toISOString(),
      isActive: r.is_active,
      company: {
        id: r.company_id, name: r.company_name,
        slug: r.company_slug, icon: r.company_icon,
      },
    })),
    total,
    ...(!userId && params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS ? { truncated: true } : {}),
  };
}

// ── Helper functions for Typesense write hooks ────────────────────────

/** Fetch owner info for Typesense watchlist doc. */
async function _getOwnerInfo(userId: string): Promise<{ name: string; username: string | null } | null> {
  const rows = await db.execute<{
    [key: string]: unknown;
    name: string;
    username: string | null;
  }>(sql`SELECT name, username FROM "user" WHERE id = ${userId} LIMIT 1`);
  const row = (rows as unknown as { name: string; username: string | null }[])[0];
  return row ?? null;
}

/** Count companies in a watchlist. */
async function _countWatchlistCompanies(watchlistId: string): Promise<number> {
  const [row] = await db.execute<{ [key: string]: unknown; cnt: number }>(
    sql`SELECT count(*)::int AS cnt FROM watchlist_company WHERE watchlist_id = ${watchlistId}`,
  );
  return (row as unknown as { cnt: number })?.cnt ?? 0;
}

/** Get the mirror count for a watchlist (number of copies). */
async function _getWatchlistMirrorCount(watchlistId: string): Promise<number> {
  const [row] = await db.execute<{ [key: string]: unknown; cnt: number }>(
    sql`SELECT count(*)::int AS cnt FROM watchlist WHERE source_watchlist_id = ${watchlistId}`,
  );
  return (row as unknown as { cnt: number })?.cnt ?? 0;
}
