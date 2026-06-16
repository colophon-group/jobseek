"use server";

import { after } from "next/server";
import { updateTag } from "next/cache";
import { eq, and, sql, type SQL } from "drizzle-orm";
import { db } from "@/db";
import {
  watchlist,
  watchlistCompany,
} from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { getViewerLanguages } from "@/lib/viewer";
import { cached, invalidate } from "@/lib/cache";
import {
  CACHE_TTL_SHORT,
  CACHE_TTL_POPULAR,
  CACHE_TTL_LONG,
} from "@/lib/cache-ttl";
import { withDbRetry } from "@/lib/db-retry";
import { watchlistCacheTag } from "@/lib/cache-tags";
import { canCreateWatchlist, getUserPlan, PLAN_LIMITS } from "@/lib/plans";
import {
  generateUniqueSlug,
  insertWatchlistWithUniqueSlug,
} from "@/lib/watchlist-slug";
import { ANON_MAX_WATCHLIST_POSTINGS, COMPANY_BATCH_SIZE } from "@/lib/search/constants";
import { expandLocationIdsBatch, resolveLocationSlugs } from "@/lib/actions/locations";
import { expandOccupationIdsBatch, resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/actions/taxonomy";
import { getSearchClient } from "@/lib/search/typesense-client";
import { buildFilterString, POSTING_BASE_FILTER, POSTING_FLOW_FILTER } from "@/lib/search/typesense-filters";
import {
  isTypesenseUnavailableError,
  withTypesenseRetry,
} from "@/lib/search/typesense-retry";
import { localesOrNoneClause } from "@/lib/search/pg-filters";
import {
  upsertWatchlist as tsUpsertWatchlist,
  deleteWatchlist as tsDeleteWatchlist,
  updateWatchlistField as tsUpdateWatchlistField,
} from "@/lib/search/typesense-watchlist";
import { isTrivialWatchlist, buildFilterCacheKey } from "@/lib/watchlist-utils";
import { notifyIndexNow, logIndexNowResult } from "@/lib/indexnow";

// ── Types ───────────────────────────────────────────────────────────

export type WatchlistFilters = {
  keywords?: string[];
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  /**
   * Work-mode (location_types) filter — `onsite | hybrid | remote`.
   * Issue #2983. Backwards-compatible: missing field on existing
   * watchlists ⇒ undefined ⇒ no filter applied. Reading code must
   * defensively re-validate strings against {@link WORK_MODE_VALUES}
   * before passing to Typesense (this column is JSONB and could carry
   * legacy garbage from older client versions).
   */
  workMode?: ("onsite" | "hybrid" | "remote")[];
  /**
   * Employment-type filter — `full_time | part_time | contract |
   * internship | temporary | volunteer`. Issue #3037 — closes the
   * parity gap between this watchlist editor and the explore page's
   * `AdvancedSearchPanel`. Same backwards-compat shape as `workMode`:
   * missing on legacy rows ⇒ undefined ⇒ no filter applied. The
   * column is JSONB and untrusted at read time; downstream consumers
   * forward values straight into Typesense `filter_by` so any future
   * sanitisation must live in `buildFilterString` (already accepts
   * `employmentTypes`).
   */
  employmentType?: string[];
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

type WatchlistPostingFilterParams = {
  companyIds: string[];
  anyCompany?: boolean;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  workMode?: ("onsite" | "hybrid" | "remote")[];
  employmentType?: string[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
};

type WatchlistPostingQueryParams = WatchlistPostingFilterParams & {
  offset: number;
  limit: number;
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

  // Slug allocation is concurrency-safe: `insertWatchlistWithUniqueSlug`
  // wraps the INSERT in a retry loop that recovers from the SELECT-then-
  // INSERT race on `idx_wl_user_slug` (#3201). Two browser tabs (or a
  // double-fire of the Create button) used to crash one of the two
  // callers with an un-handled 23505 here; the helper catches the
  // violation, regenerates a fresh `-N` suffix, and retries.
  const { row, slug } = await insertWatchlistWithUniqueSlug(
    userId,
    params.title,
    async (candidate) => {
      const [r] = await db
        .insert(watchlist)
        .values({
          userId,
          slug: candidate,
          title: params.title,
          description: params.description ?? null,
          isPublic: params.isPublic ?? true,
          filters: { anyCompany: true, ...params.filters },
        })
        .returning({ id: watchlist.id });
      return r;
    },
  );

  if (params.companyIds.length > 0) {
    await db.insert(watchlistCompany).values(
      params.companyIds.map((companyId) => ({
        watchlistId: row.id,
        companyId,
      })),
    );
  }

  // Typesense + IndexNow hook: upsert if public and non-trivial.
  // Wrapped in after() so the registration is synchronous in the
  // request scope — calling notifyIndexNow from a detached .then()
  // chain (the previous shape) silently broke because next/server's
  // after() requires a live request context to attach work.
  const isPublic = params.isPublic ?? true;
  const mergedFilters = { anyCompany: true, ...params.filters };
  const trivial = isTrivialWatchlist(mergedFilters, params.companyIds.length);

  // Cache invalidation runs unconditionally for public watchlists
  // (even trivial ones): if the URL was visited before the watchlist
  // existed, the page-level `'use cache'` may hold a null-detail
  // noindex render that needs busting. Trivial watchlists don't go
  // into Typesense / IndexNow (those flows are gated on !trivial).
  if (isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [slug]);
      } catch (err) {
        console.error("[createWatchlist] cache invalidate failed", err);
      }
    });
  }

  if (isPublic && !trivial) {
    after(async () => {
      try {
        const owner = await _getOwnerInfo(userId);
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
          is_featured: (owner.username ?? "").toLowerCase() === "colophongroup",
          has_description: !!params.description,
          created_at: Math.floor(Date.now() / 1000),
          is_public: true,
        });
        // Match sitemap semantics: only notify URLs the sitemap also exposes
        // (see apps/web/src/lib/sitemap.ts — filters `u.username IS NOT NULL`).
        if (owner.username) {
          const userSlug = owner.displayUsername ?? owner.username;
          const r = await notifyIndexNow([`/${userSlug}/${slug}`]);
          logIndexNowResult("createWatchlist", r);
        }
      } catch (err) {
        console.error("[createWatchlist] post-mutation hook failed", err);
      }
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
      filters: watchlist.filters,
    })
    .from(watchlist)
    .where(eq(watchlist.id, params.watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { error: "not_found" };

  let newSlug = wl.slug;
  const updates: Record<string, unknown> = {};

  if (params.title !== undefined) {
    updates.title = params.title;
    // The `generateUniqueSlug` call here is still subject to the same
    // SELECT-then-write race that #3201 fixed on create/copy, but in
    // the UPDATE path the race shape is benign in practice: the slug
    // is being changed on a row that already exists (same id), so two
    // concurrent renames of the same watchlist are last-write-wins on
    // the row, not a UNIQUE conflict. Cross-row collisions
    // (update-rename → slug that an unrelated row just took) remain
    // theoretically possible but out of scope for the create/copy
    // crash fix; see the issue for the analysis.
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

  // Typesense + IndexNow hook. A doc is indexed when the watchlist is
  // both public and non-trivial. after() must be called synchronously
  // here so it registers in the request scope; the awaited work runs
  // after the response is flushed but before Vercel terminates the
  // function.
  const wasPublic = wl.isPublic;
  const nowPublic = params.isPublic !== undefined ? params.isPublic : wasPublic;
  const newFilters = params.filters !== undefined
    ? params.filters
    : (wl.filters ?? {}) as WatchlistFilters;

  after(async () => {
    try {
      // Bust both cache layers so the next read of the page (and its
      // OG meta + JSON-LD) reflects the edit. Pass both old + new slug:
      // a rename leaves the old URL pointing at a stale cached entry
      // until its TTL expires. Privacy toggles + filter/companies edits
      // also flow through here. See cache-components.md "Layered TTL".
      const slugsToInvalidate = newSlug !== wl.slug ? [wl.slug, newSlug] : [wl.slug];
      await _invalidateWatchlistCaches(userId, slugsToInvalidate);

      const newCompanyCount = params.companyIds !== undefined
        ? params.companyIds.length
        : await _countWatchlistCompanies(params.watchlistId);
      const shouldIndex = nowPublic && !isTrivialWatchlist(newFilters, newCompanyCount);

      if (shouldIndex) {
        // Idempotent upsert — doc may or may not exist (public↔private or
        // trivial↔non-trivial transitions can leave stale or missing docs).
        const owner = await _getOwnerInfo(userId);
        if (!owner) return;
        const desc = params.description !== undefined ? params.description : wl.description;
        tsUpsertWatchlist({
          id: params.watchlistId,
          slug: newSlug,
          title: params.title ?? wl.title,
          description: desc ?? undefined,
          owner_name: owner.name,
          owner_username: owner.username ?? undefined,
          company_count: newCompanyCount,
          active_job_count: 0, // refreshed by reconciliation cron
          mirror_count: 0,
          is_featured: (owner.username ?? "").toLowerCase() === "colophongroup",
          has_description: !!desc,
          created_at: Math.floor(Date.now() / 1000),
          is_public: true,
        });
        if (owner.username) {
          const userSlug = owner.displayUsername ?? owner.username;
          // Notify the new URL, plus the old slug if the title rename
          // produced a new slug — the old URL now 404s and we want the
          // engines to discover that.
          const urls = [`/${userSlug}/${newSlug}`];
          if (newSlug !== wl.slug) urls.push(`/${userSlug}/${wl.slug}`);
          const r = await notifyIndexNow(urls);
          logIndexNowResult("updateWatchlist", r);
        }
      } else if (wasPublic) {
        // Was indexed and shouldn't be now — delete from Typesense and
        // ping IndexNow so engines re-crawl and discover the 404/private
        // response. IndexNow has no explicit delete; submitting the URL
        // is the canonical re-crawl trigger.
        tsDeleteWatchlist(params.watchlistId);
        const owner = await _getOwnerInfo(userId);
        if (owner?.username) {
          const userSlug = owner.displayUsername ?? owner.username;
          const r = await notifyIndexNow([`/${userSlug}/${wl.slug}`]);
          logIndexNowResult("updateWatchlist:unpublish", r);
        }
      }
    } catch (err) {
      console.error("[updateWatchlist] post-mutation hook failed", err);
    }
  });

  return { slug: newSlug };
}

export async function deleteWatchlist(
  watchlistId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db.delete(watchlist).where(eq(watchlist.id, watchlistId));

  // Typesense delete + IndexNow re-crawl trigger + Next/Redis cache
  // invalidation. The page-level `'use cache'` keeps a 1-hour cached
  // version of the public watchlist page (including OG meta + JSON-LD
  // ItemList) — without invalidating it, the deleted watchlist remains
  // visible to crawlers / unfurl previews until TTL expiry.
  after(async () => {
    try {
      await _invalidateWatchlistCaches(userId, [wl.slug]);
      tsDeleteWatchlist(watchlistId);
      if (wl.isPublic) {
        const owner = await _getOwnerInfo(userId);
        if (owner?.username) {
          const userSlug = owner.displayUsername ?? owner.username;
          const r = await notifyIndexNow([`/${userSlug}/${wl.slug}`]);
          logIndexNowResult("deleteWatchlist", r);
        }
      }
    } catch (err) {
      console.error("[deleteWatchlist] post-mutation hook failed", err);
    }
  });

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

  const sourceFilters = (source.filters ?? {}) as WatchlistFilters;

  // Same race shape as createWatchlist (#3201): two fast clicks of the
  // "Copy" button on a public watchlist used to race the SELECT-then-
  // INSERT slug pick and crash the loser. The helper retries on a
  // `idx_wl_user_slug` 23505.
  const { row, slug } = await insertWatchlistWithUniqueSlug(
    userId,
    source.title,
    async (candidate) => {
      const [r] = await db
        .insert(watchlist)
        .values({
          userId,
          slug: candidate,
          title: source.title,
          description: source.description,
          isPublic: true,
          filters: sourceFilters,
          sourceWatchlistId: watchlistId,
        })
        .returning({ id: watchlist.id });
      return r;
    },
  );

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

  // Typesense + IndexNow hooks. Wrapped in after() so work registers
  // in the request scope; the previous detached .then() pattern broke
  // notifyIndexNow because the inner after() lost its request context
  // by the time the chain resolved.
  // Cache invalidation runs unconditionally (even if trivial) — same
  // reasoning as `createWatchlist`: a stale null-detail render in the
  // page-level cache needs busting whether or not the watchlist will
  // be sitemap-indexed.
  after(async () => {
    try {
      await _invalidateWatchlistCaches(userId, [slug]);
    } catch (err) {
      console.error("[copyWatchlist] cache invalidate failed", err);
    }
  });

  if (!isTrivialWatchlist(sourceFilters, companies.length)) {
    // 1. Upsert the new copy (copies are always public) — unless trivial.
    after(async () => {
      try {
        const owner = await _getOwnerInfo(userId);
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
          is_featured: (owner.username ?? "").toLowerCase() === "colophongroup",
          has_description: !!source.description,
          created_at: Math.floor(Date.now() / 1000),
          is_public: true,
        });
        if (owner.username) {
          const userSlug = owner.displayUsername ?? owner.username;
          const r = await notifyIndexNow([`/${userSlug}/${slug}`]);
          logIndexNowResult("copyWatchlist", r);
        }
      } catch (err) {
        console.error("[copyWatchlist] Typesense upsert hook failed", err);
      }
    });
  }

  // 2. Update source watchlist's mirror_count (increment). No IndexNow
  // here — the source URL hasn't changed visible content.
  after(async () => {
    try {
      const count = await _getWatchlistMirrorCount(watchlistId);
      tsUpdateWatchlistField(watchlistId, { mirror_count: count });
    } catch (err) {
      console.error("[copyWatchlist] Typesense mirror_count hook failed", err);
    }
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

/**
 * Combined fetch for the watchlists overview page: returns the user's
 * watchlist summaries AND whether they've reached their plan limit.
 *
 * Issue #3036: the loader previously hardcoded ``limitReached: false``,
 * which meant the ``CreateWatchlistCard`` never rendered its disabled
 * state (tooltip + dimmed + upgrade modal on click). Compute the real
 * value server-side so the gating UX matches the watchlist-detail page.
 */
export async function getUserWatchlistsWithLimit(
  locale: string,
): Promise<{ watchlists: WatchlistSummary[]; limitReached: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) return { watchlists: [], limitReached: true };

  const [watchlists, limit] = await Promise.all([
    getUserWatchlists(locale),
    canCreateWatchlist(userId),
  ]);
  return { watchlists, limitReached: !limit.allowed };
}

export async function getUserWatchlists(locale: string): Promise<WatchlistSummary[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  // Viewer language preference — used to scope the `anyCompany`
  // Typesense-backed patch below so the tile count matches the
  // watchlist-detail page's locale-filtered count. The SQL fast path
  // for non-`anyCompany` rows still uses the locale-blind denormalized
  // `active_job_count` (per the trade-off in #3176 — see block comment
  // below), but the patched `anyCompany` count is exactly what the
  // detail page renders, so they must share the same filter shape.
  // See issue #3344.
  const languages = await getViewerLanguages(locale);

  // Perf (#3176): compute the active job count via a denormalized SQL
  // aggregation in the same query that loads the watchlist rows. The
  // previous shape ran N parallel `resolveFilteredJobCount` calls — one
  // Typesense round-trip per watchlist — which cost ~150-250ms for free
  // users (5 watchlists) and 1.5-2.5s for paid users (50 watchlists) on
  // every `/watchlists` load.
  //
  // The new shape returns the "company-scope" active count: SUM over
  // (watchlist's companies) of (active job_posting rows). This is the
  // same denominator the crawler's `refresh-typesense` cron writes into
  // the Typesense `watchlist.active_job_count` field — but computed
  // server-side at request time so it is fresh, and so it works for
  // private watchlists too (which never reach Typesense).
  //
  // Trade-off: this count ignores the per-watchlist filters
  // (keywords, locations, work_mode, …) and the viewer's language
  // preference. Both the listing badge and the public Discover surface
  // accept this approximation — the watchlist detail page still shows
  // the precise filter-applied count via `getWatchlistPostingDisplayCounts`.
  // The follow-up issue (#3261) tracks restoring per-viewer / per-filter
  // accuracy via a batched `multi_search` when the listing UX requires it.
  //
  // EXCEPTION (#3333): `anyCompany` watchlists have NO `watchlist_company`
  // rows (the editor toggle disables the company picker, and copies
  // already strip the rows on save), so the SQL JOIN above returns 0
  // for them — even when the filters match thousands of active postings.
  // The denormalized Typesense `active_job_count` field is also 0 for
  // these (the crawler's `refresh_typesense_counts` joins the same
  // empty `watchlist_company` table — see `apps/crawler/src/sync.py`).
  // Fall back to a per-watchlist live Typesense count for those rows
  // only, so the listing badge reflects reality. A user can have at
  // most PLAN_LIMITS.maxWatchlists.maxPaid (50) watchlists; the fan-out
  // is bounded by however many of those have `anyCompany` set, which
  // is rare in practice.
  const rows = await withDbRetry(
    () =>
      db.execute<{
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
        active_job_count: number;
      }>(sql`
        SELECT w.id, w.slug, w.title, w.description, w.is_public, w.alerts_enabled, w.filters,
               w.last_accessed_at, w.created_at,
               (SELECT count(*)::int FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_count,
               (
                 SELECT count(*)::int
                 FROM watchlist_company wc
                 JOIN job_posting jp ON jp.company_id = wc.company_id AND jp.is_active
                 WHERE wc.watchlist_id = w.id
               ) AS active_job_count
        FROM watchlist w
        WHERE w.user_id = ${userId}
        ORDER BY w.last_accessed_at DESC
      `),
    { label: "userWatchlists" },
  );

  type Row = {
    id: string; slug: string; title: string; description: string | null; is_public: boolean;
    alerts_enabled: boolean; filters: WatchlistFilters; last_accessed_at: Date; created_at: Date;
    company_count: number; active_job_count: number;
  };

  const typed = rows as unknown as Row[];

  // Patch `anyCompany` rows with a live Typesense count (see comment
  // above the SELECT). Fan-out is bounded by the user's
  // `anyCompany`-watchlist count (typically 0-2); each call is cached
  // by `_resolveAnyCompanyActiveCount` so an immediately-following
  // render hits Redis instead of Typesense. Failures degrade to the
  // SQL 0 — same as the rest of the listing surface.
  const anyCompanyRows = typed.filter((r) => r.filters?.anyCompany);
  let anyCompanyCounts: Map<string, number> = new Map();
  if (anyCompanyRows.length > 0) {
    const pairs = await Promise.all(
      anyCompanyRows.map(async (r) => {
        try {
          const c = await _resolveAnyCompanyActiveCount(r.filters, locale, languages);
          return [r.id, c] as const;
        } catch (err) {
          console.error("[getUserWatchlists] anyCompany count failed", { id: r.id, err });
          return [r.id, 0] as const;
        }
      }),
    );
    anyCompanyCounts = new Map(pairs);
  }

  return typed.map((r) => ({
    id: r.id,
    slug: r.slug,
    title: r.title,
    description: r.description,
    isPublic: r.is_public,
    alertsEnabled: r.alerts_enabled,
    companyCount: r.company_count,
    activeJobCount: r.filters?.anyCompany
      ? (anyCompanyCounts.get(r.id) ?? 0)
      : r.active_job_count,
    lastAccessedAt: new Date(r.last_accessed_at).toISOString(),
    createdAt: new Date(r.created_at).toISOString(),
  }));
}

/**
 * Compute the live "active jobs matching this watchlist's filters"
 * count against Typesense, for the `anyCompany=true` case where the
 * denormalized SQL JOIN and the Typesense `watchlist.active_job_count`
 * doc field both return 0 by construction.
 *
 * Mirrors the Typesense shape used by `_getWatchlistPostingsTypesense`
 * (POSTING_BASE_FILTER + slug-resolved filter ids + keywords +
 * viewer-language scope) but runs `per_page: 0` so we pay only for the
 * count. Cached by `buildFilterCacheKey(filters, [])` + the viewer's
 * resolved languages so two consecutive page renders with the same
 * filters and the same viewer share a single Typesense round-trip, but
 * an `en` viewer and a `de` viewer get distinct cache slots.
 *
 * Filter shape MUST match `_getWatchlistPostingsTypesense` exactly —
 * the tile count and the detail page's "active" count read the same
 * watchlist, and any divergence (e.g. omitting the locales filter)
 * shows up as a P1 count mismatch. Issue #3344 (originally allowed an
 * unscoped, broadest count here per #3262's listing trade-off; rolled
 * back when the user-visible divergence outweighed the per-viewer
 * cache fragmentation cost).
 *
 * Returns 0 on any Typesense error so the listing badge degrades to
 * the same value as the company-scope fast path.
 */
async function _resolveAnyCompanyActiveCount(
  filters: WatchlistFilters,
  locale: string,
  languages: string[],
): Promise<number> {
  const langKey = languagesCacheKey(languages);
  const key = `wl-any-active:${buildFilterCacheKey(filters, [])}:${langKey}`;
  return cached(key, async () => {
    const [locMap, occMap, senMap, techMap] = await Promise.all([
      filters.locationSlugs?.length ? resolveLocationSlugs(filters.locationSlugs, locale) : Promise.resolve(new Map()),
      filters.occupationSlugs?.length ? resolveOccupationSlugs(filters.occupationSlugs, locale) : Promise.resolve(new Map()),
      filters.senioritySlugs?.length ? resolveSenioritySlugs(filters.senioritySlugs, locale) : Promise.resolve(new Map()),
      filters.technologySlugs?.length ? resolveTechnologySlugs(filters.technologySlugs) : Promise.resolve(new Map()),
    ]);

    const filterStr = buildFilterString({
      locationIds: locMap.size > 0 ? [...locMap.values()].map((l) => l.id) : undefined,
      occupationIds: occMap.size > 0 ? [...occMap.values()].map((o) => o.id) : undefined,
      seniorityIds: senMap.size > 0 ? [...senMap.values()].map((s) => s.id) : undefined,
      technologyIds: techMap.size > 0 ? [...techMap.values()].map((t) => t.id) : undefined,
      workMode: filters.workMode?.length ? filters.workMode : undefined,
      employmentTypes: filters.employmentType?.length ? filters.employmentType : undefined,
      salaryMinEur: filters.salaryMin,
      salaryMaxEur: filters.salaryMax,
      experienceMin: filters.experienceMin,
      experienceMax: filters.experienceMax,
      languages: languages.length > 0 ? languages : undefined,
    });

    const fullFilter = `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`;
    const hasKeywords = filters.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters.keywords!.join(" ") : "*";

    try {
      const client = getSearchClient();
      const result = await client.collections("job_posting").documents().search({
        q,
        query_by: "title",
        filter_by: fullFilter,
        per_page: 0,
      });
      return result.found ?? 0;
    } catch (err) {
      console.error("[_resolveAnyCompanyActiveCount] Typesense failed", err);
      return 0;
    }
  }, { ttl: CACHE_TTL_SHORT });
}

export async function getWatchlistByUserAndSlug(
  userSlug: string,
  watchlistSlug: string,
): Promise<WatchlistDetail | null> {
  const sessionUserId = await getSessionUserId();

  // Resolve user + watchlist + companies in a single JOIN.
  //
  // Perf (#3211): the companies array used to live in a separate
  // `db.select().from(watchlist_company).innerJoin(company)…` round-trip
  // run AFTER this one. The two queries were not data-dependent in a way
  // that allowed parallelism (the second needs `wl_id` from the first)
  // but they didn't need to be sequential round-trips either — the
  // companies subquery folds cleanly into a correlated `json_agg`.
  // Mirrors the same pattern already in use for `getUserWatchlists`'s
  // denormalized `company_count` / `active_job_count`.
  type CompanyRow = {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
  type WatchlistJoinRow = {
    wl_id: string; slug: string; title: string; description: string | null;
    is_public: boolean; alerts_enabled: boolean; filters: WatchlistFilters | null;
    source_watchlist_id: string | null; created_at: Date; user_id: string;
    owner_id: string; username: string | null;
    display_username: string | null; owner_name: string;
    companies: CompanyRow[];
  };

  // URL path segment is COALESCE(display_username, username) (see sitemap.ts
  // and the IndexNow notifier) — a user with a distinct display_username
  // will advertise that variant as their slug. Match either column so the
  // detail page resolves the same URLs the sitemap exposes.  Exact username
  // match is preferred via ORDER BY when both columns happen to collide
  // across users.
  //
  // The `COALESCE(..., '[]'::json)` is load-bearing: `json_agg` returns
  // `NULL` (not `[]`) when the correlated subquery matches zero rows,
  // and every caller of this function iterates `.companies` directly
  // (`.length`, `.map`, `.slice` — see page.tsx, opengraph-image.tsx,
  // watchlist-page-data.ts).
  const rows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown } & WatchlistJoinRow>(sql`
        SELECT
          w.id AS wl_id, w.slug, w.title, w.description,
          w.is_public, w.alerts_enabled, w.filters,
          w.source_watchlist_id, w.created_at, w.user_id,
          u.id AS owner_id, u.username, u.display_username, u.name AS owner_name,
          COALESCE(
            (
              SELECT json_agg(
                json_build_object(
                  'id', c.id,
                  'name', c.name,
                  'slug', c.slug,
                  'icon', c.icon
                )
                ORDER BY c.name
              )
              FROM watchlist_company wc
              JOIN company c ON c.id = wc.company_id
              WHERE wc.watchlist_id = w.id
            ),
            '[]'::json
          ) AS companies
        FROM watchlist w
        JOIN "user" u ON u.id = w.user_id
        WHERE (u.username = ${userSlug} OR u.display_username = ${userSlug})
          AND w.slug = ${watchlistSlug}
        ORDER BY (u.username = ${userSlug})::int DESC
        LIMIT 1
      `),
    { label: `watchlistByUserAndSlug[${userSlug}/${watchlistSlug}]` },
  );

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
    companies: row.companies ?? [],
  };
}

/**
 * Public-only variant of {@link getWatchlistByUserAndSlug} that does not
 * read the request session. Returns the watchlist iff `is_public=true`,
 * regardless of viewer; private watchlists return null even for the owner.
 *
 * Use this from contexts that must stay statically prerenderable (ISR
 * pages, `generateMetadata`, sitemaps). The session-aware variant reads
 * `headers()` via `getSessionUserId()` and tainted the watchlist detail
 * page's ISR — see issue #2244.
 *
 * Wrapped in Redis `cached()` (60s TTL) so the same `(userSlug, slug)`
 * lookup deduplicates across the watchlist page's `generateMetadata`
 * and body — under cacheComponents each is a separate `'use cache'`
 * boundary running in its own clean AsyncLocalStorage, so a React-cache
 * wrapper at the page module scope no longer dedupes them.
 */
export async function getPublicWatchlistByUserAndSlug(
  userSlug: string,
  watchlistSlug: string,
): Promise<WatchlistDetail | null> {
  if (!process.env.DATABASE_URL) {
    console.warn("[watchlist] public lookup skipped because DATABASE_URL is not configured");
    return null;
  }
  return cached(
    `public-watchlist:${userSlug}:${watchlistSlug}`,
    () => _fetchPublicWatchlistByUserAndSlug(userSlug, watchlistSlug),
    { ttl: CACHE_TTL_SHORT, skipIf: (r) => r === null },
  );
}

async function _fetchPublicWatchlistByUserAndSlug(
  userSlug: string,
  watchlistSlug: string,
): Promise<WatchlistDetail | null> {
  // Same single-query fold as `getWatchlistByUserAndSlug` (#3211).
  // This variant additionally filters to `w.is_public = true` so the
  // session-free callers (ISR `generateMetadata`, OG image, blog mention
  // prerender) never accidentally leak a private watchlist.
  type CompanyRow = {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
  type WatchlistJoinRow = {
    wl_id: string; slug: string; title: string; description: string | null;
    is_public: boolean; alerts_enabled: boolean; filters: WatchlistFilters | null;
    source_watchlist_id: string | null; created_at: Date; user_id: string;
    owner_id: string; username: string | null;
    display_username: string | null; owner_name: string;
    companies: CompanyRow[];
  };

  const rows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown } & WatchlistJoinRow>(sql`
        SELECT
          w.id AS wl_id, w.slug, w.title, w.description,
          w.is_public, w.alerts_enabled, w.filters,
          w.source_watchlist_id, w.created_at, w.user_id,
          u.id AS owner_id, u.username, u.display_username, u.name AS owner_name,
          COALESCE(
            (
              SELECT json_agg(
                json_build_object(
                  'id', c.id,
                  'name', c.name,
                  'slug', c.slug,
                  'icon', c.icon
                )
                ORDER BY c.name
              )
              FROM watchlist_company wc
              JOIN company c ON c.id = wc.company_id
              WHERE wc.watchlist_id = w.id
            ),
            '[]'::json
          ) AS companies
        FROM watchlist w
        JOIN "user" u ON u.id = w.user_id
        WHERE (u.username = ${userSlug} OR u.display_username = ${userSlug})
          AND w.slug = ${watchlistSlug}
          AND w.is_public = true
        ORDER BY (u.username = ${userSlug})::int DESC
        LIMIT 1
      `),
    { label: `publicWatchlistByUserAndSlug[${userSlug}/${watchlistSlug}]` },
  );

  const row = (rows as unknown as WatchlistJoinRow[])[0];
  if (!row) return null;

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
    companies: row.companies ?? [],
  };
}

export type PublicWatchlistEntry = WatchlistSummary & {
  ownerName: string;
  ownerUsername: string | null;
  mirrorCount: number;
};

/** Stable cache-key fragment for a viewer's language filter. */
function languagesCacheKey(languages: string[] | undefined): string {
  if (!languages || languages.length === 0) return "all";
  return languages.join(",");
}

/**
 * SQL predicate: the watchlist is **not** trivial.
 *
 * Mirror of `isTrivialWatchlist` from `@/lib/watchlist-utils`. A watchlist is
 * trivial when it tracks no companies and carries no meaningful filters; we
 * use this in Postgres fallbacks for public listings to match the Typesense
 * indexing rule. Keep the two in sync.
 */
const nonTrivialWatchlistPredicate = sql`(
  (SELECT count(*) FROM watchlist_company wc WHERE wc.watchlist_id = w.id) > 0
  OR jsonb_array_length(COALESCE(w.filters->'keywords', '[]'::jsonb)) > 0
  OR jsonb_array_length(COALESCE(w.filters->'locationSlugs', '[]'::jsonb)) > 0
  OR jsonb_array_length(COALESCE(w.filters->'occupationSlugs', '[]'::jsonb)) > 0
  OR jsonb_array_length(COALESCE(w.filters->'senioritySlugs', '[]'::jsonb)) > 0
  OR jsonb_array_length(COALESCE(w.filters->'technologySlugs', '[]'::jsonb)) > 0
  OR jsonb_array_length(COALESCE(w.filters->'workMode', '[]'::jsonb)) > 0
  OR jsonb_array_length(COALESCE(w.filters->'employmentType', '[]'::jsonb)) > 0
  OR (w.filters ? 'salaryMin')
  OR (w.filters ? 'salaryMax')
  OR (w.filters ? 'experienceMin')
  OR (w.filters ? 'experienceMax')
)`;

// `buildFilterCacheKey` lives in `@/lib/watchlist-utils` so it can be unit
// tested without booting the `"use server"` module surface — `"use server"`
// modules may only export async functions, which rules out exporting the
// sync helper directly. See #3276 (follow-up to #3221).

/**
 * Count distinct companies with currently-active postings matching the given
 * watchlist filters. Used to render an accurate "Tracking N companies" string
 * in metadata for `anyCompany` watchlists, where `watchlist_company` rows are
 * unrelated to what the watchlist actually tracks.
 */
export async function getWatchlistMatchingCompanyCount(
  f: WatchlistFilters,
  languages?: string[],
): Promise<number> {
  const key = `wl-match-companies:${buildFilterCacheKey(f, [])}:${languagesCacheKey(languages)}`;
  return cached(key, async () => {
    const locale = "en";
    const [locMap, occMap, senMap, techMap] = await Promise.all([
      f.locationSlugs?.length ? resolveLocationSlugs(f.locationSlugs, locale) : Promise.resolve(new Map()),
      f.occupationSlugs?.length ? resolveOccupationSlugs(f.occupationSlugs, locale) : Promise.resolve(new Map()),
      f.senioritySlugs?.length ? resolveSenioritySlugs(f.senioritySlugs, locale) : Promise.resolve(new Map()),
      f.technologySlugs?.length ? resolveTechnologySlugs(f.technologySlugs) : Promise.resolve(new Map()),
    ]);

    const filterStr = buildFilterString({
      locationIds: locMap.size > 0 ? [...locMap.values()].map((l) => l.id) : undefined,
      occupationIds: occMap.size > 0 ? [...occMap.values()].map((o) => o.id) : undefined,
      seniorityIds: senMap.size > 0 ? [...senMap.values()].map((s) => s.id) : undefined,
      technologyIds: techMap.size > 0 ? [...techMap.values()].map((t) => t.id) : undefined,
      workMode: f.workMode?.length ? f.workMode : undefined,
      employmentTypes: f.employmentType?.length ? f.employmentType : undefined,
      salaryMinEur: f.salaryMin,
      salaryMaxEur: f.salaryMax,
      experienceMin: f.experienceMin,
      experienceMax: f.experienceMax,
      languages,
    });

    const fullFilter = `${POSTING_BASE_FILTER}${filterStr ? " && " + filterStr : ""}`;
    const hasKeywords = f.keywords && f.keywords.length > 0;
    const q = hasKeywords ? f.keywords!.join(" ") : "*";

    try {
      const client = getSearchClient();
      const result = await client.collections("job_posting").documents().search({
        q,
        query_by: "title",
        filter_by: fullFilter,
        facet_by: "company_id",
        facet_strategy: "exhaustive",
        max_facet_values: 1,
        per_page: 0,
      });
      return result.facet_counts?.[0]?.stats?.total_values ?? 0;
    } catch (err) {
      console.error("[getWatchlistMatchingCompanyCount] Typesense failed", err);
      return 0;
    }
    // Aligned to the watchlist-detail ISR window (1h, see page.tsx). Bumped
    // from 600s with #2648 — metadata freshness from a viewer's perspective
    // comes from the client-hydrated body, not the cached count.
  }, { ttl: CACHE_TTL_LONG });
}

async function queryPublicWatchlists(params: {
  whereClause: ReturnType<typeof sql>;
  orderClause: ReturnType<typeof sql>;
  offset: number;
  limit: number;
  /**
   * Currently unused — the Postgres fallback returns the same
   * "company-scope" active count the Typesense path returns
   * (denormalized, ignores filters and viewer-language scoping). See
   * the `getUserWatchlists` block comment for rationale. Kept on the
   * signature so callers still pass through their resolved viewer
   * languages — if the listing surface ever needs viewer-scoped counts
   * the parameter is already plumbed through.
   */
  languages?: string[];
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const [totalRow] = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; cnt: number }>(sql`
        SELECT count(*)::int AS cnt FROM watchlist w WHERE ${params.whereClause}
      `),
    { label: "queryPublicWatchlists.count" },
  );
  const total = (totalRow as unknown as { cnt: number })?.cnt ?? 0;
  if (total === 0) return { watchlists: [], total: 0 };

  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: string; slug: string; title: string; is_public: boolean;
        alerts_enabled: boolean; filters: WatchlistFilters;
        last_accessed_at: Date; created_at: Date;
        owner_name: string; owner_username: string | null;
        company_count: number; active_job_count: number;
        mirror_count: number;
      }>(sql`
        SELECT w.id, w.slug, w.title, w.description, w.is_public, w.alerts_enabled, w.filters,
               w.last_accessed_at, w.created_at,
               u.name AS owner_name, u.username AS owner_username,
               (SELECT count(*)::int FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_count,
               (
                 SELECT count(*)::int
                 FROM watchlist_company wc
                 JOIN job_posting jp ON jp.company_id = wc.company_id AND jp.is_active
                 WHERE wc.watchlist_id = w.id
               ) AS active_job_count,
               (SELECT count(*)::int FROM watchlist w2 WHERE w2.source_watchlist_id = w.id) AS mirror_count
        FROM watchlist w
        JOIN "user" u ON u.id = w.user_id
        WHERE ${params.whereClause}
        ORDER BY ${params.orderClause}
        OFFSET ${params.offset}
        LIMIT ${params.limit}
      `),
    { label: "queryPublicWatchlists.rows" },
  );

  type Row = {
    id: string; slug: string; title: string; description: string | null; is_public: boolean;
    alerts_enabled: boolean; filters: WatchlistFilters;
    last_accessed_at: Date; created_at: Date;
    owner_name: string; owner_username: string | null;
    company_count: number; active_job_count: number;
    mirror_count: number;
  };

  const typed = rows as unknown as Row[];

  return {
    watchlists: typed.map((r) => ({
      id: r.id,
      slug: r.slug,
      title: r.title,
      description: r.description,
      isPublic: r.is_public,
      alertsEnabled: r.alerts_enabled,
      companyCount: r.company_count,
      activeJobCount: r.active_job_count,
      lastAccessedAt: new Date(r.last_accessed_at).toISOString(),
      createdAt: new Date(r.created_at).toISOString(),
      ownerName: r.owner_name,
      ownerUsername: r.owner_username,
      mirrorCount: r.mirror_count,
    })),
    total,
  };
}

/**
 * Patch `active_job_count` on Discover-surface entries whose source
 * watchlist has `filters.anyCompany = true`.
 *
 * Context (#3352): `getPopularWatchlists` and `searchPublicWatchlists`
 * both read the denormalized `active_job_count` directly from the
 * Typesense `watchlist` doc — fast, but always 0 for `anyCompany`
 * watchlists because the crawler's `refresh_typesense_counts` joins the
 * empty `watchlist_company` table for them. Same root cause as #3333,
 * which patched the analogous code path in `getUserWatchlists`. This
 * helper mirrors that patch for the public Discover surfaces.
 *
 * Shape:
 * 1. Look up `filters` JSONB for each entry's `id` in a single batched
 *    Postgres query (Typesense doesn't index `filters`, so we can't
 *    detect `anyCompany` from the doc alone).
 * 2. For rows where `filters.anyCompany` is set, fan out one
 *    `_resolveAnyCompanyActiveCount(filters, locale, languages)` per
 *    row in parallel via `Promise.all`. Each call is cached by
 *    `_resolveAnyCompanyActiveCount` itself, so repeat renders with the
 *    same viewer-language scope hit Redis instead of Typesense.
 * 3. Patch the `activeJobCount` on the matching entry; non-anyCompany
 *    rows are returned unchanged.
 *
 * Fan-out is bounded by the page size (typically 10-20 tiles on the
 * Discover surface) times the fraction that are `anyCompany`. Same
 * trade-off as the `getUserWatchlists` patch in #3340.
 *
 * Failures (Postgres unreachable, Typesense per-row count failure)
 * degrade to the Typesense doc's denormalized 0 — same as the rest of
 * the Discover surface. Logged for visibility.
 */
async function _patchAnyCompanyCountsForDiscover(
  entries: PublicWatchlistEntry[],
  locale: string,
  languages: string[],
): Promise<PublicWatchlistEntry[]> {
  if (entries.length === 0) return entries;

  let filtersById: Map<string, WatchlistFilters>;
  try {
    const ids = entries.map((e) => e.id);
    const rows = await withDbRetry(
      () =>
        db.execute<{ [key: string]: unknown; id: string; filters: WatchlistFilters }>(sql`
          SELECT w.id, w.filters
          FROM watchlist w
          WHERE w.id = ANY(${ids}::uuid[])
        `),
      { label: "discoverAnyCompanyFilters" },
    );
    const typed = rows as unknown as { id: string; filters: WatchlistFilters }[];
    filtersById = new Map(typed.map((r) => [r.id, r.filters]));
  } catch (err) {
    console.error("[_patchAnyCompanyCountsForDiscover] filter lookup failed", err);
    return entries;
  }

  const anyCompanyEntries = entries.filter((e) => filtersById.get(e.id)?.anyCompany);
  if (anyCompanyEntries.length === 0) return entries;

  const pairs = await Promise.all(
    anyCompanyEntries.map(async (e) => {
      const f = filtersById.get(e.id)!;
      try {
        const c = await _resolveAnyCompanyActiveCount(f, locale, languages);
        return [e.id, c] as const;
      } catch (err) {
        console.error("[_patchAnyCompanyCountsForDiscover] anyCompany count failed", {
          id: e.id,
          err,
        });
        return [e.id, 0] as const;
      }
    }),
  );
  const patchedCounts = new Map(pairs);

  return entries.map((e) =>
    patchedCounts.has(e.id)
      ? { ...e, activeJobCount: patchedCounts.get(e.id)! }
      : e,
  );
}

export async function searchPublicWatchlists(params: {
  query: string;
  offset: number;
  limit: number;
  locale: string;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const q = params.query.trim();
  if (!q) return { watchlists: [], total: 0 };

  const languages = await getViewerLanguages(params.locale);
  const langKey = languagesCacheKey(languages);

  return cached(
    `public-watchlist-search:${q}:${params.offset}:${params.limit}:${langKey}`,
    async () => {
      try {
        const tsResult = await _searchPublicWatchlistsTypesense(q, params.offset, params.limit);
        if (tsResult.watchlists.length > 0) {
          // Perf (#3176): trust the denormalized `active_job_count` carried
          // on each Typesense `watchlist` doc — refreshed every 4h by the
          // crawler's `refresh-typesense` job (and inline on every CSV
          // sync). The previous shape re-computed N filtered counts per
          // listing render, costing N Typesense round-trips for a search
          // surface that shows 20+ results per page.
          //
          // EXCEPTION (#3352): `anyCompany` rows carry a denormalized 0
          // (same root cause as the `getUserWatchlists` patch in #3333 /
          // PR #3340). Patch those rows with a live Typesense count
          // bounded by the page size — see `_patchAnyCompanyCountsForDiscover`.
          const patched = await _patchAnyCompanyCountsForDiscover(
            tsResult.watchlists,
            params.locale,
            languages,
          );
          return {
            watchlists: patched,
            total: tsResult.total,
          };
        }
      } catch (err) {
        console.error("[searchPublicWatchlists] Typesense failed, falling back to Postgres", err);
      }
      // Empty Typesense result or error — fall back to Postgres
      return queryPublicWatchlists({
        whereClause: sql`w.is_public = true AND ${nonTrivialWatchlistPredicate} AND (w.title ILIKE ${"%" + q + "%"} OR w.description ILIKE ${"%" + q + "%"})`,
        orderClause: sql`w.created_at DESC`,
        offset: params.offset,
        limit: params.limit,
        languages,
      });
    },
    { ttl: CACHE_TTL_SHORT },
  );
}

export async function getPopularWatchlists(params: {
  offset: number;
  limit: number;
  locale: string;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const languages = await getViewerLanguages(params.locale);
  const langKey = languagesCacheKey(languages);

  return cached(
    `popular-watchlists:${params.offset}:${params.limit}:${langKey}`,
    async () => {
      try {
        const tsResult = await _getPopularWatchlistsTypesense(params.offset, params.limit);
        if (tsResult.watchlists.length > 0) {
          // Perf (#3176): trust the denormalized `active_job_count`
          // already on each Typesense `watchlist` doc — see the matching
          // comment in `searchPublicWatchlists`.
          //
          // EXCEPTION (#3352): patch `anyCompany` rows from their
          // structural 0 — see the matching comment in
          // `searchPublicWatchlists`.
          const patched = await _patchAnyCompanyCountsForDiscover(
            tsResult.watchlists,
            params.locale,
            languages,
          );
          return {
            watchlists: patched,
            total: tsResult.total,
          };
        }
      } catch (err) {
        console.error("[getPopularWatchlists] Typesense failed, falling back to Postgres", err);
      }
      // Empty Typesense result or error — fall back to Postgres
      return queryPublicWatchlists({
        whereClause: sql`w.is_public = true AND ${nonTrivialWatchlistPredicate}`,
        orderClause: sql`(u.username = 'colophongroup')::int DESC, (SELECT count(*)::int FROM watchlist w2 WHERE w2.source_watchlist_id = w.id) DESC, (w.description IS NOT NULL AND w.description != '')::int DESC, w.created_at DESC`,
        offset: params.offset,
        limit: params.limit,
        languages,
      });
    },
    { ttl: CACHE_TTL_POPULAR },
  );
}

export async function getWatchlistPostings(
  params: WatchlistPostingQueryParams,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
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
    if (!isTypesenseUnavailableError(err)) throw err;
    console.error("[getWatchlistPostings] Typesense failed, falling back to Postgres", err);
    return _getWatchlistPostingsPostgres(params, userId);
  }
}

/**
 * Year-window posting count for a watchlist's current filter set.
 *
 * Counterpart to `getWatchlistPostings`: same filters, but drops
 * `is_active:true` and adds `first_seen_at >= now() - 1 year`. Used to
 * feed the "N active · M in the last year" stats row on the watchlist
 * view. `per_page: 0` so Typesense returns only the `found` total with
 * no documents — cheap and cacheable.
 *
 * Composes with {@link POSTING_FLOW_FILTER} (`has_content:!=false`) so
 * the year-count stays consistent with the active-count's
 * {@link POSTING_BASE_FILTER} on the content-quality dimension — see
 * issue #3029 / follow-up to #2965. Without this, broken/empty
 * postings inflate the year badge but are correctly hidden from the
 * active badge, producing visible "active disagrees with year" rows.
 */
export async function getWatchlistPostingYearCount(
  params: WatchlistPostingFilterParams,
): Promise<number> {
  if (!params.anyCompany && params.companyIds.length === 0) return 0;
  try {
    const client = getSearchClient();
    const filterStr = buildFilterString({
      locationIds: params.locationIds,
      occupationIds: params.occupationIds,
      seniorityIds: params.seniorityIds,
      technologyIds: params.technologyIds,
      workMode: params.workMode?.length ? params.workMode : undefined,
      employmentTypes: params.employmentType?.length ? params.employmentType : undefined,
      salaryMinEur: params.salaryMin,
      salaryMaxEur: params.salaryMax,
      experienceMin: params.experienceMin,
      experienceMax: params.experienceMax,
      languages: params.languages,
    });
    const hasKeywords = params.keywords && params.keywords.length > 0;
    const keywordsQ = hasKeywords ? params.keywords!.join(" ") : "*";
    const oneYearAgo = Math.floor((Date.now() - 365 * 24 * 3600 * 1000) / 1000);
    const parts = [POSTING_FLOW_FILTER, `first_seen_at:>${oneYearAgo}`];
    if (params.companyIds.length > 0 && params.companyIds.length <= COMPANY_BATCH_SIZE) {
      parts.push(`company_id:[${params.companyIds.join(",")}]`);
    } else if (params.companyIds.length > COMPANY_BATCH_SIZE) {
      // Oversized company list: fall back to the batched helper's
      // activeTotal flavour — conservatively skip year count instead
      // of running N Typesense queries just for a stats number.
      return 0;
    }
    if (filterStr) parts.push(filterStr);
    const result = await withTypesenseRetry(
      () =>
        client.collections("job_posting").documents().search({
          q: keywordsQ,
          query_by: "title",
          filter_by: parts.join(" && "),
          per_page: 0,
        }),
      { label: "getWatchlistPostingYearCount" },
    );
    return result.found ?? 0;
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    console.error("[getWatchlistPostingYearCount] Typesense failed, falling back to Postgres", err);
    return _getWatchlistPostingYearCountPostgres(params);
  }
}

/**
 * Public-fetch posting counts for a watchlist's "N active · M last year"
 * stats row. Resolves slug filters once, then runs both counts in
 * parallel against Typesense. Session-free → ISR-safe → callable from
 * `generateMetadata` and from MDX-rendered embeds in blog posts (#2828).
 *
 * Returns `{ activeJobs: 0, yearJobs: 0 }` on Typesense error so the
 * caller can degrade gracefully (omit the stat row rather than crash).
 *
 * Languages are intentionally NOT scoped to the viewer here — like the
 * matching watchlist metadata, the broadest count is what the cached
 * SSR surface should show; viewer-scoped variants run client-side.
 */
export async function getWatchlistPostingDisplayCounts(
  detail: WatchlistDetail,
): Promise<{ activeJobs: number; yearJobs: number }> {
  const f = detail.filters;
  const isAny = f.anyCompany ?? false;
  const companyIds = detail.companies.map((c) => c.id);
  if (!isAny && companyIds.length === 0) {
    return { activeJobs: 0, yearJobs: 0 };
  }

  // Slug → id resolution mirrors the pattern in
  // `getWatchlistMatchingCompanyCount` and `resolveFilteredJobCount`.
  // Each resolver is independently cached so this fan-out is cheap on
  // the second hit per ISR window.
  const locale = "en";
  const [locMap, occMap, senMap, techMap] = await Promise.all([
    f.locationSlugs?.length ? resolveLocationSlugs(f.locationSlugs, locale) : Promise.resolve(new Map()),
    f.occupationSlugs?.length ? resolveOccupationSlugs(f.occupationSlugs, locale) : Promise.resolve(new Map()),
    f.senioritySlugs?.length ? resolveSenioritySlugs(f.senioritySlugs, locale) : Promise.resolve(new Map()),
    f.technologySlugs?.length ? resolveTechnologySlugs(f.technologySlugs) : Promise.resolve(new Map()),
  ]);

  const locationIds = locMap.size > 0 ? [...locMap.values()].map((l) => l.id) : undefined;
  const occupationIds = occMap.size > 0 ? [...occMap.values()].map((o) => o.id) : undefined;
  const seniorityIds = senMap.size > 0 ? [...senMap.values()].map((s) => s.id) : undefined;
  const technologyIds = techMap.size > 0 ? [...techMap.values()].map((t) => t.id) : undefined;

  const filterStr = buildFilterString({
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
    salaryMinEur: f.salaryMin,
    salaryMaxEur: f.salaryMax,
    experienceMin: f.experienceMin,
    experienceMax: f.experienceMax,
  });
  const hasKeywords = f.keywords && f.keywords.length > 0;
  const q = hasKeywords ? f.keywords!.join(" ") : "*";

  const companyClause = !isAny && companyIds.length > 0 && companyIds.length <= COMPANY_BATCH_SIZE
    ? `company_id:[${companyIds.join(",")}]`
    : null;
  // Oversized company list (>COMPANY_BATCH_SIZE) would need fan-out
  // batching to count correctly. The card is decorative, not load-
  // bearing — fall back to "skip the stats" rather than running 10+
  // Typesense queries for a number on a blog embed.
  if (!isAny && companyIds.length > COMPANY_BATCH_SIZE) {
    return { activeJobs: 0, yearJobs: 0 };
  }

  const baseFilterParts = (extra: string[]): string =>
    [...extra, ...(companyClause ? [companyClause] : []), ...(filterStr ? [filterStr] : [])].join(" && ");

  const oneYearAgo = Math.floor((Date.now() - 365 * 24 * 3600 * 1000) / 1000);
  const activeFilter = baseFilterParts([POSTING_BASE_FILTER]);
  // Mirror `POSTING_FLOW_FILTER` (#2965) on the year filter so the
  // year-count stays content-quality-consistent with the active filter
  // (which already includes `has_content:!=false` via POSTING_BASE_FILTER).
  // See issue #3029.
  const yearFilter = baseFilterParts([POSTING_FLOW_FILTER, `first_seen_at:>${oneYearAgo}`]);

  try {
    const client = getSearchClient();
    const [activeRes, yearRes] = await Promise.all([
      client.collections("job_posting").documents().search({
        q, query_by: "title", filter_by: activeFilter, per_page: 0,
      }),
      client.collections("job_posting").documents().search({
        q, query_by: "title", filter_by: yearFilter, per_page: 0,
      }),
    ]);
    return {
      activeJobs: activeRes.found ?? 0,
      yearJobs: yearRes.found ?? 0,
    };
  } catch (err) {
    console.error("[getWatchlistPostingDisplayCounts] Typesense failed", err);
    return { activeJobs: 0, yearJobs: 0 };
  }
}

export async function addCompanyToWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [wl] = await db
    .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .insert(watchlistCompany)
    .values({ watchlistId, companyId })
    .onConflictDoNothing();

  // The companies array drives the cached page's JSON-LD ItemList,
  // metadata description ("Jobs at X, Y, Z"), and OG image. Bust the
  // page cache + Redis layer so the change is visible on the next read.
  if (wl.isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [wl.slug]);
        const count = await _countWatchlistCompanies(watchlistId);
        tsUpdateWatchlistField(watchlistId, { company_count: count });
      } catch (err) {
        console.error("[addCompanyToWatchlist] post-mutation hook failed", err);
      }
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
    .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .delete(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));

  if (wl.isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [wl.slug]);
        tsUpdateWatchlistField(watchlistId, { company_count: 0 });
      } catch (err) {
        console.error("[clearWatchlistCompanies] post-mutation hook failed", err);
      }
    });
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
    .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
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

  if (wl.isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [wl.slug]);
        const count = await _countWatchlistCompanies(watchlistId);
        tsUpdateWatchlistField(watchlistId, { company_count: count });
      } catch (err) {
        console.error("[removeCompanyFromWatchlist] post-mutation hook failed", err);
      }
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
    sort_by: "is_featured:desc,mirror_count:desc,has_description:desc",
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

/** Max company IDs per Typesense filter string batch (~7KB ≈ 200 UUIDs). */

async function _getWatchlistPostingsTypesense(
  params: WatchlistPostingQueryParams,
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
    workMode: params.workMode?.length ? params.workMode : undefined,
    employmentTypes: params.employmentType?.length ? params.employmentType : undefined,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
    languages: params.languages,
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
  const filterParts = [POSTING_BASE_FILTER];
  if (companyFilter) filterParts.push(companyFilter);
  if (filterStr) filterParts.push(filterStr);
  const fullFilter = filterParts.join(" && ");

  const result = await withTypesenseRetry(
    () =>
      client.collections("job_posting").documents().search({
        q: keywordsQ,
        query_by: "title",
        filter_by: fullFilter,
        sort_by: hasKeywords ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
        per_page: params.limit === 0 ? 0 : params.limit,
        page: params.limit === 0 ? 1 : Math.floor(params.offset / params.limit) + 1,
      }),
    { label: "getWatchlistPostings" },
  );

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
  params: WatchlistPostingQueryParams,
  userId: string | null,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  const client = getSearchClient();

  // No expansion needed — ancestor IDs are stored on each Typesense document
  const filterStr = buildFilterString({
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    workMode: params.workMode?.length ? params.workMode : undefined,
    employmentTypes: params.employmentType?.length ? params.employmentType : undefined,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
    languages: params.languages,
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
      const filterParts = [POSTING_BASE_FILTER, `company_id:[${batch.join(",")}]`];
      if (filterStr) filterParts.push(filterStr);
      return withTypesenseRetry(
        () =>
          client.collections("job_posting").documents().search({
            q: keywordsQ,
            query_by: "title",
            filter_by: filterParts.join(" && "),
            per_page: 0,
          }),
        { label: "getWatchlistPostings.batched.count" },
      );
    }),
  );

  const total = countResults.reduce((sum, r) => sum + (r.found ?? 0), 0);
  if (total === 0 || params.limit === 0) return { postings: [], total };

  // For actual postings, query all batches with enough per_page to cover offset+limit,
  // then merge and sort by first_seen_at desc, slice to desired page.
  const needed = params.offset + params.limit;
  const postingsResults = await Promise.all(
    batches.map((batch) => {
      const filterParts = [POSTING_BASE_FILTER, `company_id:[${batch.join(",")}]`];
      if (filterStr) filterParts.push(filterStr);
      return withTypesenseRetry(
        () =>
          client.collections("job_posting").documents().search({
            q: keywordsQ,
            query_by: "title",
            filter_by: filterParts.join(" && "),
            sort_by: hasKeywords ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
            per_page: needed,
            page: 1,
          }),
        { label: "getWatchlistPostings.batched.rows" },
      );
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

async function buildWatchlistPostgresWhereClause(
  params: WatchlistPostingFilterParams,
  options: {
    activeOnly: boolean;
    firstSeenAfter?: Date;
  },
): Promise<SQL> {
  // Batched ancestor/descendant expansion: one recursive CTE per taxonomy
  // (not L per L seed IDs). The previous `Promise.all(ids.map(expand))`
  // fired L parallel recursive CTEs against `location` / `occupation` —
  // ~50–150ms of avoidable work and L extra Redis round-trips even on
  // warm cache, on the exact Postgres fallback path that runs when
  // Typesense is degraded. See #3186.
  const [expandedLocationIds, expandedOccupationIds] = await Promise.all([
    params.locationIds && params.locationIds.length > 0
      ? expandLocationIdsBatch(params.locationIds)
      : undefined,
    params.occupationIds && params.occupationIds.length > 0
      ? expandOccupationIdsBatch(params.occupationIds)
      : undefined,
  ]);

  // Mirrors the Typesense `POSTING_BASE_FILTER` / `POSTING_FLOW_FILTER`
  // content-quality checks so the Supabase fallback hides the same
  // incomplete postings: non-empty title AND an R2 description blob.
  const clauses: SQL[] = [
    sql`jp.titles IS NOT NULL AND cardinality(jp.titles) > 0 AND btrim(jp.titles[1]) <> ''`,
    sql`jp.description_r2_hash IS NOT NULL`,
  ];
  if (options.activeOnly) {
    clauses.unshift(sql`jp.is_active = true`);
  }
  if (options.firstSeenAfter) {
    clauses.push(sql`jp.first_seen_at >= ${options.firstSeenAfter}`);
  }

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
  if (params.workMode && params.workMode.length > 0) {
    // `location_types` is `text[]`. Use && (array overlap) to mirror
    // Typesense `location_types:[a,b]` (OR semantics across values).
    // Postings with NULL/empty `location_types` (~0.9% of active on
    // 2026-05-09) drop out silently — matches the Typesense path.
    const pgArr = `{${params.workMode.join(",")}}`;
    clauses.push(sql`jp.location_types && ${pgArr}::text[]`);
  }
  if (params.employmentType && params.employmentType.length > 0) {
    // `employment_type` is a single text column — use `= ANY(...)`.
    const pgArr = `{${params.employmentType.join(",")}}`;
    clauses.push(sql`jp.employment_type = ANY(${pgArr}::text[])`);
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
  const localesClause = localesOrNoneClause(params.languages);
  if (localesClause) clauses.push(localesClause);

  return sql.join(clauses, sql` AND `);
}

async function _getWatchlistPostingYearCountPostgres(
  params: WatchlistPostingFilterParams,
): Promise<number> {
  const oneYearAgo = new Date(Date.now() - 365 * 24 * 3600 * 1000);
  const whereClause = await buildWatchlistPostgresWhereClause(params, {
    activeOnly: false,
    firstSeenAfter: oneYearAgo,
  });

  const [row] = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; cnt: number }>(
        sql`SELECT count(*)::int AS cnt FROM job_posting jp WHERE ${whereClause}`,
      ),
    { label: "getWatchlistPostingYearCountPostgres.count" },
  );
  return (row as unknown as { cnt: number })?.cnt ?? 0;
}

/** Postgres fallback for getWatchlistPostings (graceful degradation). */
async function _getWatchlistPostingsPostgres(
  params: WatchlistPostingQueryParams,
  userId: string | null,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  const whereClause = await buildWatchlistPostgresWhereClause(params, {
    activeOnly: true,
  });

  const [totalRow] = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; cnt: number }>(
        sql`SELECT count(*)::int AS cnt FROM job_posting jp WHERE ${whereClause}`,
      ),
    { label: "getWatchlistPostingsPostgres.count" },
  );
  const total = (totalRow as unknown as { cnt: number })?.cnt ?? 0;
  if (total === 0 || params.limit === 0) return { postings: [], total };

  const rows = await withDbRetry(
    () =>
      db.execute<{
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
      `),
    { label: "getWatchlistPostingsPostgres.rows" },
  );

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

/**
 * Invalidate every cache layer that could be holding a public watchlist's
 * pre-mutation state: the per-region `'use cache'` page entry (tagged via
 * `watchlistCacheTag`) AND the Redis-backed `cached("public-watchlist:...")`
 * SQL fetch. Required for both privacy toggles AND title renames AND
 * filter/companies edits — without this, the watchlist page (and its OG
 * meta tags + JSON-LD ItemList) keep showing the pre-edit state for up to
 * cacheLife.revalidate (1 hour for /[user]/[watchlist]).
 *
 * Pass every slug variant that the visitor might hit: the new slug after
 * a rename AND the old slug (which now 404s but is cached). Also covers
 * both `username` and `displayUsername` since the public route accepts
 * either as the user-segment.
 */
async function _invalidateWatchlistCaches(
  userId: string,
  slugs: string[],
): Promise<void> {
  const owner = await _getOwnerInfo(userId);
  if (!owner) return;
  const userSlugs = new Set<string>();
  if (owner.username) userSlugs.add(owner.username);
  if (owner.displayUsername) userSlugs.add(owner.displayUsername);
  if (userSlugs.size === 0) return;

  for (const userSlug of userSlugs) {
    for (const slug of slugs) {
      // `updateTag` (not `revalidateTag`) — we need immediate eviction
      // for the privacy / rename / delete flows. `revalidateTag(tag, "hours")`
      // would only mark the cache entry stale within a 24h SWR window:
      // the next visitor would still see the pre-mutation render.
      // `updateTag` invalidates so the next read fetches fresh DB data.
      updateTag(watchlistCacheTag(userSlug, slug));
      try {
        await invalidate(`public-watchlist:${userSlug}:${slug}`);
      } catch (err) {
        console.error("[invalidateWatchlistCaches] redis invalidate failed", err);
      }
    }
  }
}

/** Fetch owner info for Typesense watchlist doc + IndexNow URL construction. */
async function _getOwnerInfo(
  userId: string,
): Promise<{ name: string; username: string | null; displayUsername: string | null } | null> {
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        name: string;
        username: string | null;
        display_username: string | null;
      }>(sql`SELECT name, username, display_username FROM "user" WHERE id = ${userId} LIMIT 1`),
    { label: `ownerInfo[${userId}]` },
  );
  const row = (rows as unknown as { name: string; username: string | null; display_username: string | null }[])[0];
  if (!row) return null;
  return { name: row.name, username: row.username, displayUsername: row.display_username };
}

/** Count companies in a watchlist. */
async function _countWatchlistCompanies(watchlistId: string): Promise<number> {
  const [row] = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; cnt: number }>(
        sql`SELECT count(*)::int AS cnt FROM watchlist_company WHERE watchlist_id = ${watchlistId}`,
      ),
    { label: `countWatchlistCompanies[${watchlistId}]` },
  );
  return (row as unknown as { cnt: number })?.cnt ?? 0;
}

/** Get the mirror count for a watchlist (number of copies). */
async function _getWatchlistMirrorCount(watchlistId: string): Promise<number> {
  const [row] = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; cnt: number }>(
        sql`SELECT count(*)::int AS cnt FROM watchlist WHERE source_watchlist_id = ${watchlistId}`,
      ),
    { label: `watchlistMirrorCount[${watchlistId}]` },
  );
  return (row as unknown as { cnt: number })?.cnt ?? 0;
}
