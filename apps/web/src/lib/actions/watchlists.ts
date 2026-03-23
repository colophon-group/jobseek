"use server";

import { eq, and, sql } from "drizzle-orm";
import { db } from "@/db";
import {
  watchlist,
  watchlistCompany,
  company,
  user,
} from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { canCreateWatchlist, getUserPlan, PLAN_LIMITS } from "@/lib/plans";
import { generateUniqueSlug } from "@/lib/watchlist-slug";
import { expandLocationIds, resolveLocationSlugs } from "@/lib/actions/locations";
import { expandOccupationIds, resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/actions/taxonomy";

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
      filters: params.filters ?? {},
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
    .select({ id: watchlist.id, userId: watchlist.userId, slug: watchlist.slug })
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

  // Compute active job counts respecting each watchlist's filters
  const counts = await Promise.all(
    typed.map((r) => resolveFilteredJobCount(r.filters ?? {}, r.company_ids ?? [])),
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

  // Resolve user by username
  const [owner] = await db
    .select({
      id: user.id,
      username: user.username,
      displayUsername: user.displayUsername,
      name: user.name,
    })
    .from(user)
    .where(eq(user.username, userSlug))
    .limit(1);

  if (!owner) return null;

  const [wl] = await db
    .select({
      id: watchlist.id,
      slug: watchlist.slug,
      title: watchlist.title,
      description: watchlist.description,
      isPublic: watchlist.isPublic,
      alertsEnabled: watchlist.alertsEnabled,
      filters: watchlist.filters,
      sourceWatchlistId: watchlist.sourceWatchlistId,
      createdAt: watchlist.createdAt,
      userId: watchlist.userId,
    })
    .from(watchlist)
    .where(
      and(eq(watchlist.userId, owner.id), eq(watchlist.slug, watchlistSlug)),
    )
    .limit(1);

  if (!wl) return null;

  // Access check: public or owner
  if (!wl.isPublic && wl.userId !== sessionUserId) return null;

  // Touch lastAccessedAt if owner
  if (wl.userId === sessionUserId) {
    await db
      .update(watchlist)
      .set({ lastAccessedAt: new Date() })
      .where(eq(watchlist.id, wl.id));
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
    .where(eq(watchlistCompany.watchlistId, wl.id))
    .orderBy(company.name);

  return {
    id: wl.id,
    slug: wl.slug,
    title: wl.title,
    description: wl.description,
    isPublic: wl.isPublic,
    alertsEnabled: wl.alertsEnabled,
    filters: (wl.filters ?? {}) as WatchlistFilters,
    sourceWatchlistId: wl.sourceWatchlistId,
    createdAt: wl.createdAt.toISOString(),
    owner: {
      id: owner.id,
      username: owner.username,
      displayUsername: owner.displayUsername,
      name: owner.name,
    },
    companies,
  };
}

export type PublicWatchlistEntry = WatchlistSummary & {
  ownerName: string;
  ownerUsername: string | null;
  mirrorCount: number;
};

async function resolveFilteredJobCount(
  f: WatchlistFilters,
  companyIds: string[],
): Promise<number> {
  const isAny = f.anyCompany;
  if (!isAny && companyIds.length === 0) return 0;

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

  // Compute filtered job counts in parallel
  const counts = await Promise.all(
    typed.map((r) => resolveFilteredJobCount(r.filters ?? {}, r.company_ids ?? [])),
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

  return queryPublicWatchlists({
    whereClause: sql`w.is_public = true AND (w.title ILIKE ${"%" + q + "%"} OR w.description ILIKE ${"%" + q + "%"})`,
    orderClause: sql`w.created_at DESC`,
    offset: params.offset,
    limit: params.limit,
  });
}

export async function getPopularWatchlists(params: {
  offset: number;
  limit: number;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  return queryPublicWatchlists({
    whereClause: sql`w.is_public = true`,
    orderClause: sql`(SELECT count(*)::int FROM watchlist w2 WHERE w2.source_watchlist_id = w.id) DESC, w.created_at DESC`,
    offset: params.offset,
    limit: params.limit,
  });
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
}): Promise<{ postings: WatchlistPostingEntry[]; total: number }> {
  // No companies selected and not "any company" mode → empty
  if (!params.anyCompany && params.companyIds.length === 0) {
    return { postings: [], total: 0 };
  }

  // Expand parent locations/occupations to include children (e.g. Switzerland → Zurich)
  const [expandedLocationIds, expandedOccupationIds] = await Promise.all([
    params.locationIds && params.locationIds.length > 0
      ? Promise.all(params.locationIds.map(expandLocationIds)).then((a) => [...new Set(a.flat())])
      : undefined,
    params.occupationIds && params.occupationIds.length > 0
      ? Promise.all(params.occupationIds.map(expandOccupationIds)).then((a) => [...new Set(a.flat())])
      : undefined,
  ]);

  // Build filter clauses
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
  };
}

export async function addCompanyToWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .insert(watchlistCompany)
    .values({ watchlistId, companyId })
    .onConflictDoNothing();

  return { ok: true };
}

export async function clearWatchlistCompanies(
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

  await db
    .delete(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));

  return { ok: true };
}

export async function removeCompanyFromWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId })
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

  return { ok: true };
}
