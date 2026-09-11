import "server-only";

import { createHash } from "node:crypto";
import { after } from "next/server";
import { revalidatePath, updateTag } from "next/cache";
import { eq, and, sql } from "drizzle-orm";
import { db } from "@/db";
import {
  watchlist,
  watchlistCompany,
  userPreferences,
} from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { siteConfig } from "@/content/config";
import { getViewerLanguages } from "@/lib/viewer";
import { resolveJobLanguages } from "@/lib/job-languages";
import { convertToEur } from "@/lib/salary";
import {
  cached,
  invalidate,
  kvMget,
  kvSet,
} from "@/lib/cache";
import {
  CACHE_TTL_SHORT,
  CACHE_TTL_POPULAR,
  CACHE_TTL_LONG,
} from "@/lib/cache-ttl";
import { withDbRetry } from "@/lib/db-retry";
import { watchlistCacheTag } from "@/lib/cache-tags";
import { canCreateWatchlist } from "@/lib/plans";
import {
  createWithinWatchlistLimit,
  WatchlistLimitReachedError,
} from "@/lib/watchlist-limit";
import {
  authorizeWatchlistCopySource,
  type WatchlistCopySourceKind,
} from "@/lib/watchlist-copy-policy";
import {
  generateUniqueSlug,
  insertWatchlistWithUniqueSlug,
} from "@/lib/watchlist-slug";
import { ANON_MAX_WATCHLIST_POSTINGS, COMPANY_BATCH_SIZE } from "@/lib/search/constants";
import { resolveLocationSlugs } from "@/lib/actions/locations";
import { getCurrencyRates } from "@/lib/services/search";
import { resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/services/taxonomy";
import { getSearchClient } from "@/lib/search/typesense-client";
import { logExternalError } from "@/lib/safe-external-error";
import { buildFilterString, POSTING_BASE_FILTER, POSTING_FLOW_FILTER } from "@/lib/search/typesense-filters";
import {
  assertTypesenseSearchResult,
  isTypesenseUnavailableError,
  malformedTypesenseResponseError,
  withTypesenseRetry,
} from "@/lib/search/typesense-retry";
import { splitValuesForTypesenseQuery } from "@/lib/search/typesense-query-size";
import { deleteWatchlist as tsDeleteWatchlist } from "@/lib/search/typesense-watchlist";
import { buildFilterCacheKey } from "@/lib/watchlist-utils";
import { notifyIndexNow, logIndexNowResult } from "@/lib/indexnow";
import { createWatchlistFromHandoffWithDeps } from "@/lib/services/watchlist-handoff";
import { publicWatchlistRouteStatusCacheKey } from "@/lib/services/public-resource-status";
import { toggleWatchlistAlertState } from "@/lib/notifications/policy";
import { lockNotificationPolicyForUser } from "@/lib/services/notification-preferences";
import { sharedWatchlistCloneLimiter } from "@/lib/rate-limit";
import { readWatchlistCandidates } from "@/lib/services/watchlist-matcher";
import {
  normalizeCreateWatchlistInput,
  normalizeHandoffWatchlistInput,
  normalizeSharedWatchlistMetadata,
  normalizeUpdateWatchlistInput,
  normalizeWatchlistCompaniesForRead,
  normalizeWatchlistCompanyIdsForRead,
  normalizeWatchlistFiltersForRead,
  normalizeWatchlistFiltersForSharedRead,
  normalizeWatchlistUuid,
  WATCHLIST_COMPANY_MAX,
} from "@/lib/services/watchlist-input";
import type {
  WatchlistCandidateFilters,
  WatchlistFilters,
  WatchlistPostingEntry,
} from "@/lib/watchlist-matcher-contract";

export type {
  WatchlistFilters,
  WatchlistPostingEntry,
} from "@/lib/watchlist-matcher-contract";

// ── Types ───────────────────────────────────────────────────────────

type WorkMode = NonNullable<WatchlistFilters["workMode"]>[number];

type IndexedWatchlistFilters = WatchlistFilters & {
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
};

const WORK_MODE_VALUES = new Set<WorkMode>(["onsite", "hybrid", "remote"]);

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

export type UserWatchlistOverview = Omit<WatchlistSummary, "activeJobCount" | "isPublic"> & {
  isShared: boolean;
  /** Loaded after the overview renders so count aggregation cannot block navigation. */
  activeJobCount: number | null;
};

export type UserWatchlistActivityPreview = {
  activeJobCount: number;
  /** Exact number of distinct companies contributing at least one matching active job. */
  activeCompanyCount: number;
  /** Highest-activity matching companies, ordered by active job count. */
  topCompanies: Array<{
    id: string;
    name: string;
    icon: string | null;
  }>;
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

/**
 * The only watchlist fields allowed across the RSC boundary into the result UI.
 * Owner identity, source lineage, legacy visibility, slugs, and timestamps stay
 * server-only, including for anonymous shared-link viewers.
 */
export type WatchlistViewDetail = Pick<
  WatchlistDetail,
  "id" | "title" | "description" | "filters" | "companies"
> & {
  /** Present for owner views only; shared snapshots do not expose alert state. */
  alertsEnabled?: boolean;
};

type WatchlistPostingFilterParams = WatchlistCandidateFilters & {
  abortSignal?: AbortSignal;
  /** Let an orchestrating page distinguish provider failure from a real zero. */
  failOnUnavailable?: boolean;
};

type WatchlistPostingQueryParams = WatchlistPostingFilterParams & {
  offset: number;
  limit: number;
};

type WatchlistAuditAction =
  | "watchlist.create"
  | "watchlist.update"
  | "watchlist.delete"
  | "watchlist.share"
  | "watchlist.copy"
  | "watchlist.company.add"
  | "watchlist.companies.clear"
  | "watchlist.company.remove";

type WatchlistAuditPayload = {
  event: "watchlist.audit";
  occurred_at: string;
  action: WatchlistAuditAction;
  user_ref: string;
  watchlist_ref: string;
  slug_before?: string | null;
  slug_after?: string | null;
  is_public_before?: boolean | null;
  is_public_after?: boolean | null;
  share_enabled_before?: boolean | null;
  share_enabled_after?: boolean | null;
  copy_source_kind?: WatchlistCopySourceKind;
  company_count_delta?: number | null;
};

type WatchlistAuditInput = Omit<
  WatchlistAuditPayload,
  "event" | "occurred_at" | "user_ref" | "watchlist_ref"
> & {
  userId: string;
  watchlist_id: string;
};

function _watchlistAuditUserRef(userId: string): string {
  return createHash("sha256").update(userId).digest("hex").slice(0, 12);
}

function _logWatchlistAudit({ userId, watchlist_id, ...entry }: WatchlistAuditInput): void {
  const payload: WatchlistAuditPayload = {
    event: "watchlist.audit",
    occurred_at: new Date().toISOString(),
    user_ref: _watchlistAuditUserRef(userId),
    watchlist_ref: createHash("sha256")
      .update(watchlist_id)
      .digest("hex")
      .slice(0, 12),
    ...entry,
  };
  console.info(JSON.stringify(payload));
}

function _revalidatePrivateWatchlistOverview(): void {
  try {
    revalidatePath("/[lang]/(app)/watchlists", "page");
  } catch (err) {
    logExternalError(
      "warn",
      { service: "external_http", operation: "revalidate_watchlist_overview" },
      err,
    );
  }
}

class WatchlistCopySourceUnavailableError extends Error {}
class WatchlistCopySourceTooLargeError extends Error {}
class WatchlistCopySourceInvalidError extends Error {}

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

  // Bounded compatibility for callers that still send the retired field:
  // false is harmless, but an attempt to create a public row fails before the
  // slug lookup, capacity lock, or transaction. All persisted creates below
  // are private by construction.
  if (params && typeof params === "object" && params.isPublic === true) {
    return { error: "visibility_locked" };
  }

  const normalized = normalizeCreateWatchlistInput(params);
  if (!normalized.ok) return { error: "invalid_input" };
  params = normalized.value;

  // Slug allocation is concurrency-safe: `insertWatchlistWithUniqueSlug`
  // wraps the INSERT in a retry loop that recovers from the SELECT-then-
  // INSERT race on `idx_wl_user_slug` (#3201). Two browser tabs (or a
  // double-fire of the Create button) used to crash one of the two
  // callers with an un-handled 23505 here; the helper catches the
  // violation, regenerates a fresh `-N` suffix, and retries.
  //
  // Keep the transaction *inside* the retry callback. A 23505 aborts
  // its transaction in Postgres; allowing that transaction to reject
  // before the helper retries gives every slug candidate a fresh
  // transaction while keeping the parent + company rows atomic.
  const inserted = await insertWatchlistWithUniqueSlug(
    userId,
    params.title,
    async (candidate) =>
      db.transaction((tx) =>
        createWithinWatchlistLimit(tx, userId, async () => {
          const [r] = await tx
            .insert(watchlist)
            .values({
              userId,
              slug: candidate,
              title: params.title,
              description: params.description ?? null,
              isPublic: false,
              filters: { anyCompany: true, ...params.filters },
            })
            .returning({ id: watchlist.id });

          if (params.companyIds.length > 0) {
            await tx.insert(watchlistCompany).values(
              params.companyIds.map((companyId) => ({
                watchlistId: r.id,
                companyId,
              })),
            );
          }

          return r;
        }),
      ),
  ).catch((error: unknown) => {
    if (error instanceof WatchlistLimitReachedError) return null;
    throw error;
  });

  if (!inserted) return { error: "limit_reached" };
  const { row, slug } = inserted;

  _logWatchlistAudit({
    action: "watchlist.create",
    userId,
    watchlist_id: row.id,
    slug_before: null,
    slug_after: slug,
    is_public_before: null,
    is_public_after: false,
    company_count_delta: params.companyIds.length,
  });
  _revalidatePrivateWatchlistOverview();

  return { id: row.id, slug };
}

export async function createWatchlistFromHandoff(params: {
  title: string;
  description?: string;
  companySlugs: string[];
  filters?: WatchlistFilters;
}): Promise<{ id: string; slug: string } | { error: string }> {
  const normalized = normalizeHandoffWatchlistInput(params);
  if (!normalized.ok) return { error: "invalid_input" };

  // Keep the company-detail cache/search module off the ordinary watchlist
  // import path. It is needed only for this explicit public-API handoff.
  const { getCompanyIdsBySlugs } = await import(
    "@/lib/services/company-detail"
  );
  return createWatchlistFromHandoffWithDeps(normalized.value, {
    getCompanyIdsBySlugs,
    createWatchlist,
  });
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

  const normalized = normalizeUpdateWatchlistInput(params);
  if (!normalized.ok) return { error: "invalid_input" };
  params = normalized.value;

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

  // Visibility is no longer mutable through the domain service. Keep the
  // field in the input temporarily so the backend can fail closed while the
  // separate UI cutover removes legacy toggles.
  if (params.isPublic !== undefined) return { error: "visibility_locked" };

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
  if (Object.keys(updates).length > 0 || params.companyIds !== undefined) {
    await db.transaction(async (tx) => {
      if (params.companyIds !== undefined) {
        // Copy takes a shared lock on this row before reading company
        // membership. Coordinate company-only edits with that lock so a copy
        // observes one coherent source version under READ COMMITTED.
        await tx
          .select({ id: watchlist.id })
          .from(watchlist)
          .where(eq(watchlist.id, params.watchlistId))
          .for("update")
          .limit(1);
      }

      if (Object.keys(updates).length > 0) {
        await tx
          .update(watchlist)
          .set(updates)
          .where(eq(watchlist.id, params.watchlistId));
      }

      if (params.companyIds !== undefined) {
        await tx
          .delete(watchlistCompany)
          .where(eq(watchlistCompany.watchlistId, params.watchlistId));

        if (params.companyIds.length > 0) {
          await tx.insert(watchlistCompany).values(
            params.companyIds.map((companyId) => ({
              watchlistId: params.watchlistId,
              companyId,
            })),
          );
        }
      }
    });
  }

  // Typesense + IndexNow hook. A doc is indexed when the watchlist is
  // both public and non-trivial. after() must be called synchronously
  // here so it registers in the request scope; the awaited work runs
  // after the response is flushed but before Vercel terminates the
  // function.
  const wasPublic = wl.isPublic;
  const didUpdateWatchlist = Object.keys(updates).length > 0;
  const didReplaceCompanies = params.companyIds !== undefined;

  if (didUpdateWatchlist || didReplaceCompanies) {
    _logWatchlistAudit({
      action: "watchlist.update",
      userId,
      watchlist_id: params.watchlistId,
      slug_before: wl.slug,
      slug_after: newSlug,
      is_public_before: wasPublic,
      is_public_after: wasPublic,
      company_count_delta: didReplaceCompanies ? null : undefined,
    });
  }

  after(async () => {
    try {
      // Bust both cache layers so the next read of the page (and its
      // OG meta + JSON-LD) reflects the edit. Pass both old + new slug:
      // a rename leaves the old URL pointing at a stale cached entry
      // until its TTL expires. Filter/company edits to grandfathered public
      // rows also flow through here until migration. See cache-components.md
      // "Layered TTL".
      const slugsToInvalidate = newSlug !== wl.slug ? [wl.slug, newSlug] : [wl.slug];
      await _invalidateWatchlistCaches(userId, slugsToInvalidate);

      if (wasPublic) {
        // Public discovery is retired. Editing a grandfathered row removes
        // any stale search document instead of refreshing it.
        await _unindexPublicWatchlist(
          userId,
          params.watchlistId,
          [wl.slug],
          "updateWatchlist:retired-public-cleanup",
        );
      }
    } catch (err) {
      logExternalError("error", { service: "external_http", operation: "update_watchlist_hook" }, err);
    }
  });

  if (didUpdateWatchlist || didReplaceCompanies) {
    _revalidatePrivateWatchlistOverview();
  }
  return { slug: newSlug };
}

/** Make an owned watchlist available at its opaque, unlisted id URL. */
export async function shareWatchlist(
  watchlistId: string,
): Promise<{ ok: true; url: string } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return { error: "invalid_input" };
  watchlistId = normalizedWatchlistId;

  const eligibility = await db.transaction(async (tx) => {
    const [owned] = await tx
      .select({
        id: watchlist.id,
        userId: watchlist.userId,
        title: watchlist.title,
        description: watchlist.description,
        filters: watchlist.filters,
        shareEnabled: watchlist.shareEnabled,
      })
      .from(watchlist)
      .where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, userId)))
      .for("update")
      .limit(1);
    if (!owned || owned.userId !== userId) return { error: "not_found" } as const;
    if (
      !normalizeSharedWatchlistMetadata(owned) ||
      !normalizeWatchlistFiltersForSharedRead(owned.filters)
    ) {
      return { error: "invalid_source" } as const;
    }
    const companies = await tx
      .select({ companyId: watchlistCompany.companyId })
      .from(watchlistCompany)
      .where(eq(watchlistCompany.watchlistId, watchlistId))
      .limit(WATCHLIST_COMPANY_MAX + 1);
    if (companies.length > WATCHLIST_COMPANY_MAX) {
      return { error: "too_large" } as const;
    }
    if (owned.shareEnabled) return { changed: false } as const;

    await tx
      .update(watchlist)
      .set({ shareEnabled: true })
      .where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, userId)));
    return { changed: true } as const;
  });

  if ("error" in eligibility && typeof eligibility.error === "string") {
    return { error: eligibility.error };
  }
  if ("changed" in eligibility && eligibility.changed) {
    _logWatchlistAudit({
      action: "watchlist.share",
      userId,
      watchlist_id: watchlistId,
      share_enabled_before: false,
      share_enabled_after: true,
    });
    _revalidatePrivateWatchlistOverview();
  }

  // Sharing is deliberately unlisted: no Typesense, sitemap, or IndexNow hook.
  return {
    ok: true,
    url: new URL(`/watchlists/${watchlistId}`, siteConfig.url).toString(),
  };
}

export async function deleteWatchlist(
  watchlistId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return { ok: false };
  watchlistId = normalizedWatchlistId;

  const [wl] = await db
    .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db.delete(watchlist).where(eq(watchlist.id, watchlistId));

  _logWatchlistAudit({
    action: "watchlist.delete",
    userId,
    watchlist_id: watchlistId,
    slug_before: wl.slug,
    slug_after: null,
    is_public_before: wl.isPublic,
    is_public_after: null,
    company_count_delta: null,
  });

  // Typesense delete + IndexNow re-crawl trigger + Next/Redis cache
  // invalidation. The page-level `'use cache'` keeps a 1-hour cached
  // version of the public watchlist page (including OG meta + JSON-LD
  // ItemList) — without invalidating it, the deleted watchlist remains
  // visible to crawlers / unfurl previews until TTL expiry.
  after(async () => {
    try {
      await _invalidateWatchlistCaches(userId, [wl.slug]);
      await _unindexPublicWatchlist(
        userId,
        watchlistId,
        wl.isPublic ? [wl.slug] : [],
        "deleteWatchlist",
      );
    } catch (err) {
      logExternalError("error", { service: "external_http", operation: "delete_watchlist_hook" }, err);
    }
  });

  _revalidatePrivateWatchlistOverview();

  return { ok: true };
}

async function _copyWatchlist(
  watchlistId: string,
  requestedSourceKind: WatchlistCopySourceKind,
): Promise<{ id: string; slug: string } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return { error: "invalid_input" };
  watchlistId = normalizedWatchlistId;

  // Server Actions can be replayed against a different pathname, so the
  // shared-link mutation owns its abuse boundary instead of relying only on
  // route-scoped proxy matching.
  if (requestedSourceKind === "share") {
    const { success } = await sharedWatchlistCloneLimiter.limit(userId);
    if (!success) return { error: "rate_limited" };
  }

  const [source] = await db
    .select({
      title: watchlist.title,
      userId: watchlist.userId,
      isShared: watchlist.shareEnabled,
    })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  const sourceAuthorization = source
    ? authorizeWatchlistCopySource(source, userId, requestedSourceKind)
    : null;
  if (!sourceAuthorization) {
    return { error: "not_found" };
  }
  const sourceTitle = normalizeSharedWatchlistMetadata({
    title: source.title,
    description: null,
  })?.title;
  if (!sourceTitle) return { error: "invalid_source" };

  // Same race shape as createWatchlist (#3201): two fast clicks of the
  // "Copy" button on a watchlist used to race the SELECT-then-
  // INSERT slug pick and crash the loser. The helper retries on a
  // `idx_wl_user_slug` 23505. As in create, each retry attempt owns a
  // fresh transaction so a rejected candidate never poisons the next. Re-read
  // and authorize the source inside that transaction too: the first read is
  // only a slug-allocation hint. The shared source-row lock coordinates with
  // edits so the fields and company rows belong to one coherent version.
  let inserted;
  try {
    inserted = await insertWatchlistWithUniqueSlug(
      userId,
      sourceTitle,
      async (candidate) =>
        db.transaction(async (tx) =>
          createWithinWatchlistLimit(tx, userId, async () => {
            const [currentSource] = await tx
              .select({
                title: watchlist.title,
                description: watchlist.description,
                filters: watchlist.filters,
                userId: watchlist.userId,
                isShared: watchlist.shareEnabled,
              })
              .from(watchlist)
              .where(eq(watchlist.id, watchlistId))
              .for("share")
              .limit(1);

            const currentAuthorization = currentSource
              ? authorizeWatchlistCopySource(
                currentSource,
                userId,
                sourceAuthorization.sourceKind,
              )
              : null;
            if (!currentAuthorization) {
              throw new WatchlistCopySourceUnavailableError();
            }
            const metadata = normalizeSharedWatchlistMetadata(currentSource);
            const filters = normalizeWatchlistFiltersForSharedRead(currentSource.filters);
            if (!metadata || !filters) {
              throw new WatchlistCopySourceInvalidError();
            }

            const [r] = await tx
              .insert(watchlist)
              .values({
                userId,
                slug: candidate,
                title: metadata.title,
                description: metadata.description,
                isPublic: false,
                filters,
                sourceWatchlistId: watchlistId,
              })
              .returning({ id: watchlist.id });

            // Copy companies (even in anyCompany mode, so toggling it off reveals them).
            // The parent and child rows share a transaction so a malformed/stale
            // company reference cannot leave behind an empty partial copy.
            const companies = await tx
              .select({ companyId: watchlistCompany.companyId })
              .from(watchlistCompany)
              .where(eq(watchlistCompany.watchlistId, watchlistId))
              .limit(WATCHLIST_COMPANY_MAX + 1);

            if (companies.length > WATCHLIST_COMPANY_MAX) {
              throw new WatchlistCopySourceTooLargeError();
            }

            if (companies.length > 0) {
              await tx.insert(watchlistCompany).values(
                companies.map((c) => ({
                  watchlistId: r.id,
                  companyId: c.companyId,
                })),
              );
            }

            return {
              row: r,
              companies,
              sourceKind: currentAuthorization.sourceKind,
            };
          }),
        ),
    );
  } catch (error) {
    if (error instanceof WatchlistCopySourceUnavailableError) {
      return { error: "not_found" };
    }
    if (error instanceof WatchlistCopySourceTooLargeError) {
      return { error: "source_too_large" };
    }
    if (error instanceof WatchlistCopySourceInvalidError) {
      return { error: "invalid_source" };
    }
    if (error instanceof WatchlistLimitReachedError) {
      return { error: "limit_reached" };
    }
    throw error;
  }

  const { row: copyResult, slug } = inserted;
  const { row, companies, sourceKind } = copyResult;

  _logWatchlistAudit({
    action: "watchlist.copy",
    userId,
    watchlist_id: row.id,
    slug_before: null,
    slug_after: slug,
    is_public_before: null,
    is_public_after: false,
    copy_source_kind: sourceKind,
    company_count_delta: companies.length,
  });

  // Typesense + IndexNow hooks. Wrapped in after() so work registers
  // in the request scope; the previous detached .then() pattern broke
  // notifyIndexNow because the inner after() lost its request context
  // by the time the chain resolved.
  // Cache invalidation runs unconditionally (even if private/trivial) — same
  // reasoning as `createWatchlist`: a stale null-detail render in the
  // page-level cache needs busting whether or not the watchlist will
  // be sitemap-indexed.
  after(async () => {
    try {
      await _invalidateWatchlistCaches(userId, [slug]);
    } catch (err) {
      logExternalError("error", { service: "redis", operation: "copy_watchlist_invalidate" }, err);
    }
  });

  // Copies are private by construction, so they never enter Typesense,
  // sitemaps, or IndexNow.

  _revalidatePrivateWatchlistOverview();

  return { id: row.id, slug };
}

export function copyWatchlist(
  watchlistId: string,
): Promise<{ id: string; slug: string } | { error: string }> {
  return _copyWatchlist(watchlistId, "owned");
}

export function copySharedWatchlist(
  watchlistId: string,
): Promise<{ id: string; slug: string } | { error: string }> {
  return _copyWatchlist(watchlistId, "share");
}

export async function toggleWatchlistAlerts(
  watchlistId: string,
): Promise<{ enabled: boolean } | { error: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return { error: "invalid_input" };
  watchlistId = normalizedWatchlistId;

  return db.transaction(async (tx) => {
    await lockNotificationPolicyForUser(tx, userId);
    const changedAt = new Date();

    const [wl] = await tx
      .select({
        alertsEnabled: watchlist.alertsEnabled,
        alertsEnabledAt: watchlist.alertsEnabledAt,
      })
      .from(watchlist)
      .where(
        and(eq(watchlist.id, watchlistId), eq(watchlist.userId, userId)),
      )
      .for("update")
      .limit(1);

    if (!wl) return { error: "not_found" };

    // Older accounts may not have materialized a preference row yet. Create
    // the conservative weekly/unpaused baseline so every enabled alert has a
    // persisted global state timestamp available to later window policy.
    await tx
      .insert(userPreferences)
      .values({
        userId,
        notificationsStateChangedAt: changedAt,
      })
      .onConflictDoNothing({ target: userPreferences.userId });

    const [preferences] = await tx
      .select({ notificationsPaused: userPreferences.notificationsPaused })
      .from(userPreferences)
      .where(eq(userPreferences.userId, userId))
      .limit(1);

    const transition = toggleWatchlistAlertState(
      wl,
      preferences?.notificationsPaused ?? false,
      changedAt,
    );
    if (!transition.ok) return { error: transition.error };

    await tx
      .update(watchlist)
      .set(transition.state)
      .where(
        and(eq(watchlist.id, watchlistId), eq(watchlist.userId, userId)),
      );

    return { enabled: transition.state.alertsEnabled };
  });
}

/**
 * Combined fetch for the watchlists overview page: returns the user's
 * watchlist summaries AND whether they've reached the universal account limit.
 *
 * Issue #3036: the loader previously hardcoded ``limitReached: false``,
 * which meant the ``CreateWatchlistCard`` never rendered its disabled
 * state (tooltip + dimmed + explanatory modal on click). Compute the real
 * value server-side so the limit UX matches the watchlist-detail page.
 */
export async function getUserWatchlistsWithLimit(
  _locale: string,
): Promise<{ watchlists: UserWatchlistOverview[]; limitReached: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) return { watchlists: [], limitReached: true };

  const [rows, limit] = await Promise.all([
    _getUserWatchlistRows(userId),
    canCreateWatchlist(userId),
  ]);
  return {
    watchlists: rows.map((row) => _toUserWatchlistSummary(row, null)),
    limitReached: !limit.allowed,
  };
}

type UserWatchlistRow = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  is_public: boolean;
  share_enabled: boolean;
  alerts_enabled: boolean;
  filters: WatchlistFilters;
  last_accessed_at: Date;
  created_at: Date;
  company_count: number;
  company_ids: string[];
};

async function _getUserWatchlistRows(userId: string): Promise<UserWatchlistRow[]> {
  // Keep the database portion bounded to watchlist metadata and company
  // membership. Counting active postings here used to join every tracked
  // company against the full `job_posting` history for every watchlist;
  // production users with larger lists exceeded a 15-second statement
  // timeout even with an active-company index (#5896).
  //
  // Active counts are intentionally resolved outside the initial page load.
  const rows = await withDbRetry(
    () =>
      db.execute<UserWatchlistRow & { [key: string]: unknown }>(sql`
        SELECT w.id, w.slug, w.title, w.description, w.is_public, w.share_enabled, w.alerts_enabled, w.filters,
               w.last_accessed_at, w.created_at,
               (SELECT count(*)::int FROM watchlist_company wc WHERE wc.watchlist_id = w.id) AS company_count,
               (
                 SELECT COALESCE(array_agg(wc.company_id::text ORDER BY wc.company_id::text), ARRAY[]::text[])
                 FROM watchlist_company wc
                 WHERE wc.watchlist_id = w.id
               ) AS company_ids
        FROM watchlist w
        WHERE w.user_id = ${userId}
        ORDER BY w.last_accessed_at DESC, w.created_at DESC, w.id ASC
      `),
    { label: "userWatchlists" },
  );

  return (rows as unknown as UserWatchlistRow[]).map((row) => ({
    ...row,
    filters: normalizeWatchlistFiltersForRead(row.filters),
    company_ids: normalizeWatchlistCompanyIdsForRead(row.company_ids),
  }));
}

function _toUserWatchlistSummary(
  row: UserWatchlistRow,
  activeJobCount: number | null,
): UserWatchlistOverview {
  return {
    id: row.id,
    slug: row.slug,
    title: row.title,
    description: row.description,
    isShared: row.share_enabled,
    alertsEnabled: row.alerts_enabled,
    companyCount: row.company_count,
    activeJobCount,
    lastAccessedAt: new Date(row.last_accessed_at).toISOString(),
    createdAt: new Date(row.created_at).toISOString(),
  };
}

async function _getUserWatchlistsForUser(
  userId: string,
  locale: string,
  loadLanguages: () => Promise<string[]> = () => getViewerLanguages(locale),
): Promise<UserWatchlistOverview[]> {
  // Viewer language preference is used by the batched Typesense count so
  // listing counts match the watchlist-detail page.
  const [rows, languages] = await Promise.all([
    _getUserWatchlistRows(userId),
    loadLanguages(),
  ]);

  const preciseCounts = await _resolveUserListingCounts(rows, locale, languages);
  return rows.map((row) => ({
    ..._toUserWatchlistSummary(row, preciseCounts.get(row.id) ?? 0),
    activeJobCount: preciseCounts.get(row.id) ?? 0,
  }));
}

export async function getUserWatchlists(locale: string): Promise<UserWatchlistOverview[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  return _getUserWatchlistsForUser(userId, locale);
}

async function _getViewerLanguagesForUser(
  userId: string,
  locale: string,
): Promise<string[]> {
  const rows = await withDbRetry(
    () =>
      db.execute<{ job_languages: string[] | null }>(sql`
        SELECT job_languages
        FROM user_preferences
        WHERE user_id = ${userId}
        LIMIT 1
      `),
    { label: "userWatchlistLanguages" },
  );

  return resolveJobLanguages(rows[0]?.job_languages ?? [], locale);
}

export async function getUserWatchlistCountsForUser(
  userId: string,
  locale: string,
): Promise<Record<string, number>> {
  const watchlists = await _getUserWatchlistsForUser(
    userId,
    locale,
    () => _getViewerLanguagesForUser(userId, locale),
  );
  return Object.fromEntries(
    watchlists.map((watchlist) => [watchlist.id, watchlist.activeJobCount ?? 0]),
  );
}

/**
 * Load the private overview's activity preview without an N+1 fan-out.
 *
 * All watchlists share one faceted Typesense multi-search. The at-most-four
 * leading company IDs from each result are then hydrated in one bounded SQL
 * query so the API never trusts denormalized display metadata from postings.
 */
export async function getUserWatchlistActivityPreviewsForUser(
  userId: string,
  locale: string,
): Promise<Record<string, UserWatchlistActivityPreview>> {
  const [rows, languages] = await Promise.all([
    _getUserWatchlistRows(userId),
    _getViewerLanguagesForUser(userId, locale),
  ]);
  const previews = await _resolveUserActivityPreviews(rows, locale, languages);
  return Object.fromEntries(previews);
}

export async function getUserWatchlistCounts(locale: string): Promise<Record<string, number>> {
  const watchlists = await getUserWatchlists(locale);
  return Object.fromEntries(
    watchlists.map((watchlist) => [watchlist.id, watchlist.activeJobCount ?? 0]),
  );
}

type WatchlistDetailCompanyRow = {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
};

type WatchlistDetailRow = {
  wl_id: string;
  slug: string;
  title: string;
  description: string | null;
  is_public: boolean;
  alerts_enabled: boolean;
  filters: WatchlistFilters | null;
  source_watchlist_id: string | null;
  created_at: Date;
  owner_id: string;
  username: string | null;
  display_username: string | null;
  owner_name: string;
  companies: WatchlistDetailCompanyRow[];
};

function _watchlistDetail(row: WatchlistDetailRow): WatchlistDetail {
  return {
    id: row.wl_id,
    slug: row.slug,
    title: row.title,
    description: row.description,
    isPublic: row.is_public,
    alertsEnabled: row.alerts_enabled,
    filters: normalizeWatchlistFiltersForRead(row.filters),
    sourceWatchlistId: row.source_watchlist_id,
    createdAt: new Date(row.created_at).toISOString(),
    owner: {
      id: row.owner_id,
      username: row.username,
      displayUsername: row.display_username,
      name: row.owner_name,
    },
    companies: normalizeWatchlistCompaniesForRead(row.companies) ?? [],
  };
}

async function _getWatchlistDetailRow(
  predicate: ReturnType<typeof sql>,
  label: string,
): Promise<WatchlistDetailRow | null> {
  const rows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown } & WatchlistDetailRow>(sql`
        SELECT
          w.id AS wl_id, w.slug, w.title, w.description,
          w.is_public, w.alerts_enabled, w.filters,
          w.source_watchlist_id, w.created_at,
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
        WHERE ${predicate}
        LIMIT 1
      `),
    { label },
  );
  return (rows as unknown as WatchlistDetailRow[])[0] ?? null;
}

async function _getOwnedWatchlistDetail(
  userId: string,
  predicate: ReturnType<typeof sql>,
  label: string,
): Promise<WatchlistDetail | null> {
  const row = await _getWatchlistDetailRow(
    sql`w.user_id = ${userId} AND ${predicate}`,
    label,
  );
  if (!row) return null;

  after(async () => {
    try {
      await db
        .update(watchlist)
        .set({ lastAccessedAt: new Date() })
        .where(and(eq(watchlist.id, row.wl_id), eq(watchlist.userId, userId)));
    } catch (err) {
      logExternalError(
        "error",
        { service: "database", operation: "touch_owned_watchlist_access" },
        err,
      );
    }
  });

  return _watchlistDetail(row);
}

/** Resolve an opaque selection hint with ownership in the SQL predicate. */
export async function getOwnedWatchlistById(
  watchlistId: string,
  userId: string,
): Promise<WatchlistDetail | null> {
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return null;
  return _getOwnedWatchlistDetail(
    userId,
    sql`w.id = ${normalizedWatchlistId}`,
    "ownedWatchlistById",
  );
}

/** Resolve an unlisted shared watchlist by its opaque id. */
export async function getSharedWatchlistById(
  watchlistId: string,
): Promise<WatchlistViewDetail | null> {
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return null;
  type SharedWatchlistRow = Pick<
    WatchlistDetailRow,
    "wl_id" | "title" | "description" | "filters" | "companies"
  > & { companies_over_limit: boolean };
  const rows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown } & SharedWatchlistRow>(sql`
        SELECT
          w.id AS wl_id, w.title, w.description, w.filters,
          EXISTS (
            SELECT 1
            FROM watchlist_company over_limit
            WHERE over_limit.watchlist_id = w.id
            OFFSET ${WATCHLIST_COMPANY_MAX}
            LIMIT 1
          ) AS companies_over_limit,
          COALESCE(
            (
              SELECT json_agg(
                json_build_object(
                  'id', bounded_company.id,
                  'name', bounded_company.name,
                  'slug', bounded_company.slug,
                  'icon', bounded_company.icon
                )
                ORDER BY bounded_company.name
              )
              FROM (
                SELECT c.id, c.name, c.slug, c.icon
                FROM watchlist_company wc
                JOIN company c ON c.id = wc.company_id
                WHERE wc.watchlist_id = w.id
                ORDER BY c.name
                LIMIT ${WATCHLIST_COMPANY_MAX}
              ) bounded_company
            ),
            '[]'::json
          ) AS companies
        FROM watchlist w
        WHERE w.id = ${normalizedWatchlistId} AND w.share_enabled = true
        LIMIT 1
      `),
    { label: "sharedWatchlistById" },
  );
  const row = (rows as unknown as SharedWatchlistRow[])[0];
  if (!row || row.companies_over_limit) return null;
  const metadata = normalizeSharedWatchlistMetadata(row);
  if (!metadata) return null;
  const filters = normalizeWatchlistFiltersForSharedRead(row.filters);
  if (!filters) return null;
  const companies = normalizeWatchlistCompaniesForRead(
    row.companies,
    WATCHLIST_COMPANY_MAX,
  );
  if (!companies) return null;
  return {
    id: row.wl_id,
    title: metadata.title,
    description: metadata.description,
    filters,
    companies,
  };
}

/** Bounded compatibility lookup for the former username/slug URL. */
export async function getOwnedWatchlistByLegacyPath(
  userSlug: string,
  watchlistSlug: string,
  userId: string,
): Promise<WatchlistDetail | null> {
  return _getOwnedWatchlistDetail(
    userId,
    sql`w.slug = ${watchlistSlug}
      AND (u.username = ${userSlug} OR u.display_username = ${userSlug})`,
    "ownedWatchlistByLegacyPath",
  );
}

async function _resolveUserListingCounts(
  rows: Array<{
    id: string;
    filters: WatchlistFilters | null;
    company_ids: string[] | null;
  }>,
  locale: string,
  languages: string[],
): Promise<Map<string, number>> {
  const candidates = await _buildUserListingCandidates(rows, locale);
  return resolvePreciseListingCounts(candidates, languages, "getUserWatchlists");
}

async function _buildUserListingCandidates(
  rows: Array<{
    id: string;
    filters: WatchlistFilters | null;
    company_ids: string[] | null;
  }>,
  locale: string,
): Promise<ListingCountCandidate[]> {
  const filteredRows = rows.filter((r) => hasPreciseListingCountFilters(r.filters));

  let indexedById = new Map<string, IndexedWatchlistFilters | null>();
  if (filteredRows.length > 0) {
    try {
      indexedById = await buildIndexedFiltersForUserRows(filteredRows, locale);
    } catch (err) {
      logExternalError("error", { service: "typesense", operation: "user_watchlist_filter_ids" }, err);
    }
  }

  const candidates: ListingCountCandidate[] = [];
  for (const row of rows) {
    const needsResolvedIds = hasPreciseListingCountFilters(row.filters);
    const filters = needsResolvedIds
      ? indexedById.get(row.id)
      : buildIndexedWatchlistFiltersPayload(row.filters);
    if (!filters || hasUnresolvedIndexedTaxonomy(filters)) continue;
    candidates.push({
      id: row.id,
      filters,
      companyIds: row.company_ids ?? [],
      fallbackCount: 0,
    });
  }
  return candidates;
}

async function buildIndexedFiltersForUserRows(
  rows: Array<{ id: string; filters: WatchlistFilters | null }>,
  locale: string,
): Promise<Map<string, IndexedWatchlistFilters | null>> {
  const locationSlugs = new Set<string>();
  const occupationSlugs = new Set<string>();
  const senioritySlugs = new Set<string>();
  const technologySlugs = new Set<string>();

  for (const row of rows) {
    const f = row.filters ?? {};
    f.locationSlugs?.forEach((slug) => locationSlugs.add(slug));
    f.occupationSlugs?.forEach((slug) => occupationSlugs.add(slug));
    f.senioritySlugs?.forEach((slug) => senioritySlugs.add(slug));
    f.technologySlugs?.forEach((slug) => technologySlugs.add(slug));
  }

  const [locMap, occMap, senMap, techMap] = await Promise.all([
    locationSlugs.size > 0 ? resolveLocationSlugs([...locationSlugs], locale) : Promise.resolve(new Map()),
    occupationSlugs.size > 0 ? resolveOccupationSlugs([...occupationSlugs], locale) : Promise.resolve(new Map()),
    senioritySlugs.size > 0 ? resolveSenioritySlugs([...senioritySlugs], locale) : Promise.resolve(new Map()),
    technologySlugs.size > 0 ? resolveTechnologySlugs([...technologySlugs]) : Promise.resolve(new Map()),
  ]);

  const result = new Map<string, IndexedWatchlistFilters | null>();
  for (const row of rows) {
    const filters = row.filters ?? {};
    const resolvedIds = {
      locationIds: idsForSlugs(filters.locationSlugs, locMap),
      occupationIds: idsForSlugs(filters.occupationSlugs, occMap),
      seniorityIds: idsForSlugs(filters.senioritySlugs, senMap),
      technologyIds: idsForSlugs(filters.technologySlugs, techMap),
    };
    const payload = buildIndexedWatchlistFiltersPayload(filters, resolvedIds);
    result.set(row.id, payload);
  }
  return result;
}

function idsForSlugs<T extends { id: number }>(
  slugs: string[] | undefined,
  resolved: Map<string, T>,
): number[] | undefined {
  if (!slugs?.length) return undefined;
  const ids: number[] = [];
  for (const slug of slugs) {
    const match = resolved.get(slug);
    if (!match) return undefined;
    ids.push(match.id);
  }
  return ids;
}

function hasPreciseListingCountFilters(
  filters: WatchlistFilters | IndexedWatchlistFilters | null | undefined,
): boolean {
  if (!filters) return false;
  return Boolean(
    filters.anyCompany ||
      filters.keywords?.length ||
      filters.locationSlugs?.length ||
      filters.occupationSlugs?.length ||
      filters.senioritySlugs?.length ||
      filters.technologySlugs?.length ||
      filters.workMode?.length ||
      filters.employmentType?.length ||
      filters.salaryMin != null ||
      filters.salaryMax != null ||
      filters.experienceMin != null ||
      filters.experienceMax != null,
  );
}

async function resolvePreciseListingCounts(
  candidates: ListingCountCandidate[],
  languages: string[],
  _label: string,
): Promise<Map<string, number>> {
  if (candidates.length === 0) return new Map();

  const counts = new Map<string, number>();
  const searchable: ListingCountCandidate[] = [];
  for (const candidate of candidates) {
    if (!candidate.filters.anyCompany && candidate.companyIds.length === 0) {
      counts.set(candidate.id, 0);
    } else {
      searchable.push(candidate);
    }
  }
  if (searchable.length === 0) return counts;

  const searches = searchable.map((candidate) =>
    buildListingCountSearch(candidate.filters, candidate.companyIds, languages),
  );

  try {
    const client = getSearchClient();
    const result = await client.multiSearch.perform({ searches });
    const results = (result as { results?: Array<{ found?: number }> }).results ?? [];
    searchable.forEach((candidate, index) => {
      counts.set(candidate.id, results[index]?.found ?? candidate.fallbackCount);
    });
  } catch (err) {
    logExternalError(
      "error",
      { service: "typesense", operation: "precise_listing_count" },
      err,
    );
    for (const candidate of searchable) counts.set(candidate.id, candidate.fallbackCount);
  }

  return counts;
}

function buildListingCountSearch(
  filters: IndexedWatchlistFilters,
  companyIds: string[],
  languages: string[],
): ListingCountSearch {
  const filterStr = buildFilterString({
    locationIds: filters.locationIds?.length ? filters.locationIds : undefined,
    occupationIds: filters.occupationIds?.length ? filters.occupationIds : undefined,
    seniorityIds: filters.seniorityIds?.length ? filters.seniorityIds : undefined,
    technologyIds: filters.technologyIds?.length ? filters.technologyIds : undefined,
    workMode: filters.workMode?.length ? filters.workMode : undefined,
    employmentTypes: filters.employmentType?.length ? filters.employmentType : undefined,
    salaryMinEur: filters.salaryMin,
    salaryMaxEur: filters.salaryMax,
    experienceMin: filters.experienceMin,
    experienceMax: filters.experienceMax,
    languages: languages.length > 0 ? languages : undefined,
  });
  const q = filters.keywords?.length ? filters.keywords.join(" ") : "*";
  return {
    collection: "job_posting",
    q,
    query_by: "title",
    filter_by: buildWatchlistPostingFilter(
      [POSTING_BASE_FILTER],
      filters.anyCompany ? [] : companyIds,
      filterStr,
    ),
    per_page: 0,
  };
}

function buildListingActivitySearch(
  filters: IndexedWatchlistFilters,
  companyIds: readonly string[],
  languages: string[],
): ListingActivitySearch {
  return {
    ...buildListingCountSearch(filters, [...companyIds], languages),
    facet_by: "company_id",
    facet_strategy: "exhaustive",
    max_facet_values: 4,
  };
}

type ListingActivitySearchResult = {
  error?: unknown;
  found?: number;
  facet_counts?: Array<{
    field_name?: string;
    counts?: Array<{ value?: unknown; count?: unknown }>;
    stats?: { total_values?: unknown };
  }>;
};

type UniqueListingActivityPlan = {
  cacheKey: string;
  search: ListingActivitySearch;
  watchlistIds: Set<string>;
};

function listingActivityPlanCacheKey(search: ListingActivitySearch): string {
  const digest = createHash("sha256")
    .update(JSON.stringify(search))
    .digest("hex");
  return `watchlist-activity:v1:${digest}`;
}

function isCacheableListingActivityResult(
  value: unknown,
): value is ListingActivitySearchResult {
  if (!value || typeof value !== "object") return false;
  const result = value as ListingActivitySearchResult;
  if (
    result.error != null ||
    typeof result.found !== "number" ||
    !Number.isFinite(result.found) ||
    result.found < 0
  ) {
    return false;
  }

  const companyFacet = result.facet_counts?.find(
    (facet) => facet.field_name === "company_id",
  );
  if (result.found === 0 && !companyFacet) return true;
  const totalValues = companyFacet?.stats?.total_values;
  return Boolean(
    companyFacet &&
      Array.isArray(companyFacet.counts) &&
      typeof totalValues === "number" &&
      Number.isFinite(totalValues) &&
      totalValues >= 0 &&
      companyFacet.counts.every(
        (entry) =>
          typeof entry.value === "string" &&
          typeof entry.count === "number" &&
          Number.isFinite(entry.count) &&
          entry.count >= 0,
      ),
  );
}

async function resolveListingActivityPlanResults(
  plans: UniqueListingActivityPlan[],
): Promise<Map<string, ListingActivitySearchResult>> {
  const results = new Map<string, ListingActivitySearchResult>();
  let cacheHits: Array<ListingActivitySearchResult | null> = plans.map(() => null);
  try {
    cacheHits = await kvMget<ListingActivitySearchResult>(
      plans.map((plan) => plan.cacheKey),
    );
  } catch (err) {
    logExternalError(
      "warn",
      { service: "redis", operation: "watchlist_activity_preview_cache_read" },
      err,
    );
  }

  const missingPlans: UniqueListingActivityPlan[] = [];
  plans.forEach((plan, index) => {
    const hit = cacheHits[index];
    if (isCacheableListingActivityResult(hit)) {
      results.set(plan.cacheKey, hit);
    } else {
      missingPlans.push(plan);
    }
  });
  if (missingPlans.length === 0) return results;

  try {
    const client = getSearchClient();
    const response = await client.multiSearch.perform({
      searches: missingPlans.map((plan) => plan.search),
    });
    const responseResults = (response as {
      results?: ListingActivitySearchResult[];
    }).results;
    if (
      !Array.isArray(responseResults) ||
      responseResults.length !== missingPlans.length
    ) {
      throw malformedTypesenseResponseError();
    }

    const cacheWrites: Promise<void>[] = [];
    missingPlans.forEach((plan, index) => {
      const result = responseResults[index];
      if (!isCacheableListingActivityResult(result)) return;
      results.set(plan.cacheKey, result);
      cacheWrites.push(kvSet(plan.cacheKey, result, { ttl: CACHE_TTL_SHORT }));
    });
    await Promise.all(cacheWrites);
  } catch (err) {
    logExternalError(
      "error",
      { service: "typesense", operation: "watchlist_activity_preview" },
      err,
    );
  }

  return results;
}

async function _resolveUserActivityPreviews(
  rows: Array<{
    id: string;
    filters: WatchlistFilters | null;
    company_ids: string[] | null;
  }>,
  locale: string,
  languages: string[],
): Promise<Map<string, UserWatchlistActivityPreview>> {
  const emptyPreview = (): UserWatchlistActivityPreview => ({
    activeJobCount: 0,
    activeCompanyCount: 0,
    topCompanies: [],
  });
  // A missing entry means the activity service could not determine the
  // answer. Keep that distinct from an explicit zero so outages and
  // unresolved legacy filters never masquerade as "no active jobs".
  const previews = new Map<string, UserWatchlistActivityPreview>();
  const needsCurrencyRates = rows.some((row) => {
    const filters = row.filters;
    return Boolean(
      (filters?.salaryMin != null || filters?.salaryMax != null) &&
        (filters.salaryCurrency ?? "EUR") !== "EUR",
    );
  });
  const [candidates, currencyRates] = await Promise.all([
    _buildUserListingCandidates(rows, locale),
    needsCurrencyRates ? getCurrencyRates() : Promise.resolve([]),
  ]);
  const searchable: ListingCountCandidate[] = [];
  for (const candidate of candidates) {
    if (!candidate.filters.anyCompany && candidate.companyIds.length === 0) {
      previews.set(candidate.id, emptyPreview());
    } else {
      searchable.push(candidate);
    }
  }
  if (searchable.length === 0) return previews;

  const plansByCacheKey = new Map<string, UniqueListingActivityPlan>();
  for (const candidate of searchable) {
    const salaryCurrency = candidate.filters.salaryCurrency ?? "EUR";
    const filters = {
      ...candidate.filters,
      salaryMin: convertToEur(
        candidate.filters.salaryMin,
        salaryCurrency,
        currencyRates,
      ),
      salaryMax: convertToEur(
        candidate.filters.salaryMax,
        salaryCurrency,
        currencyRates,
      ),
    };
    const companyIds = candidate.filters.anyCompany
      ? []
      : [...new Set(candidate.companyIds)].sort();
    const buildSearch = (batch: readonly string[]) =>
      buildListingActivitySearch(filters, batch, languages);
    const batches = filters.anyCompany
      ? [[]]
      : splitValuesForTypesenseQuery(
          companyIds,
          buildSearch,
          COMPANY_BATCH_SIZE,
        );
    for (const batch of batches) {
      const search = buildSearch(batch);
      const cacheKey = listingActivityPlanCacheKey(search);
      const plan = plansByCacheKey.get(cacheKey) ?? {
        cacheKey,
        search,
        watchlistIds: new Set<string>(),
      };
      plan.watchlistIds.add(candidate.id);
      plansByCacheKey.set(cacheKey, plan);
    }
  }
  const plans = [...plansByCacheKey.values()];
  if (plans.length === 0) return previews;

  const results = await resolveListingActivityPlanResults(plans);

  const aggregates = new Map<string, ListingActivityAggregate>();
  const invalidWatchlistIds = new Set<string>();
  plans.forEach((plan) => {
    const result = results.get(plan.cacheKey);
    for (const watchlistId of plan.watchlistIds) {
      if (invalidWatchlistIds.has(watchlistId)) continue;
      try {
        if (
          !result ||
          result.error != null ||
          typeof result.found !== "number" ||
          !Number.isFinite(result.found) ||
          result.found < 0
        ) {
          throw malformedTypesenseResponseError();
        }
        const aggregate = aggregates.get(watchlistId) ?? {
          activeJobCount: 0,
          activeCompanyCount: 0,
          companyCounts: new Map<string, number>(),
        };
        aggregate.activeJobCount += result.found;
        const companyFacet = result.facet_counts?.find(
          (facet) => facet.field_name === "company_id",
        );
        const totalValues = companyFacet?.stats?.total_values;
        if (
          result.found > 0 &&
          (!companyFacet ||
            !Array.isArray(companyFacet.counts) ||
            typeof totalValues !== "number" ||
            !Number.isFinite(totalValues) ||
            totalValues < 0)
        ) {
          throw malformedTypesenseResponseError();
        }
        if (typeof totalValues === "number" && Number.isFinite(totalValues)) {
          // Explicit-company batches contain disjoint IDs, so their exact
          // distinct totals can be summed without double-counting.
          aggregate.activeCompanyCount += Math.max(0, totalValues);
        }
        for (const facetCount of companyFacet?.counts ?? []) {
          if (
            typeof facetCount.value !== "string" ||
            typeof facetCount.count !== "number" ||
            !Number.isFinite(facetCount.count) ||
            facetCount.count < 0
          ) {
            throw malformedTypesenseResponseError();
          }
          aggregate.companyCounts.set(
            facetCount.value,
            (aggregate.companyCounts.get(facetCount.value) ?? 0) +
              Math.max(0, facetCount.count),
          );
        }
        aggregates.set(watchlistId, aggregate);
      } catch {
        // A multi-search can return an error envelope for one slot while
        // sibling searches succeed. Drop the whole affected watchlist (all of
        // its split plans must be valid for exact totals), but retain siblings.
        invalidWatchlistIds.add(watchlistId);
        aggregates.delete(watchlistId);
      }
    }
  });
  if (invalidWatchlistIds.size > 0) {
    logExternalError(
      "error",
      { service: "typesense", operation: "watchlist_activity_preview" },
      malformedTypesenseResponseError(),
    );
  }

  const topCompanyIdsByWatchlist = new Map<string, string[]>();
  const allTopCompanyIds = new Set<string>();
  for (const [watchlistId, aggregate] of aggregates) {
    const topCompanyIds = [...aggregate.companyCounts.entries()]
      .sort(([leftId, leftCount], [rightId, rightCount]) =>
        rightCount - leftCount || (leftId < rightId ? -1 : leftId > rightId ? 1 : 0),
      )
      .slice(0, 4)
      .map(([companyId]) => companyId);
    topCompanyIdsByWatchlist.set(watchlistId, topCompanyIds);
    topCompanyIds.forEach((companyId) => allTopCompanyIds.add(companyId));
    previews.set(watchlistId, {
      activeJobCount: aggregate.activeJobCount,
      activeCompanyCount: aggregate.activeCompanyCount,
      topCompanies: [],
    });
  }

  if (allTopCompanyIds.size === 0) return previews;

  let companiesById: Map<string, { id: string; name: string; icon: string | null }>;
  try {
    companiesById = await _hydrateActivityPreviewCompanies([...allTopCompanyIds]);
  } catch (err) {
    logExternalError(
      "error",
      { service: "database", operation: "hydrate_watchlist_activity_companies" },
      err,
    );
    return previews;
  }

  for (const [watchlistId, companyIds] of topCompanyIdsByWatchlist) {
    const preview = previews.get(watchlistId);
    if (!preview) continue;
    preview.topCompanies = companyIds.flatMap((companyId) => {
      const company = companiesById.get(companyId);
      return company ? [company] : [];
    });
  }
  return previews;
}

async function _hydrateActivityPreviewCompanies(
  companyIds: string[],
): Promise<Map<string, { id: string; name: string; icon: string | null }>> {
  if (companyIds.length === 0) return new Map();
  const pgArray = pgTextArrayLiteral(companyIds);
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: string;
        name: string;
        icon: string | null;
      }>(sql`
        SELECT c.id::text AS id, c.name, c.icon
        FROM company c
        WHERE c.id = ANY(${pgArray}::uuid[])
      `),
    { label: "watchlistActivityPreviewCompanies" },
  );
  return new Map(
    (rows as unknown as Array<{ id: string; name: string; icon: string | null }>).map(
      (company) => [company.id, company],
    ),
  );
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
                  'id', bounded_company.id,
                  'name', bounded_company.name,
                  'slug', bounded_company.slug,
                  'icon', bounded_company.icon
                )
                ORDER BY bounded_company.name
              )
              FROM (
                SELECT c.id, c.name, c.slug, c.icon
                FROM watchlist_company wc
                JOIN company c ON c.id = wc.company_id
                WHERE wc.watchlist_id = w.id
                ORDER BY c.name
                LIMIT ${WATCHLIST_COMPANY_MAX}
              ) bounded_company
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

  // Touch lastAccessedAt after the response; it is private analytics state
  // and should not affect the watchlist detail read path.
  if (row.user_id === sessionUserId) {
    after(async () => {
      try {
        await db
          .update(watchlist)
          .set({ lastAccessedAt: new Date() })
          .where(eq(watchlist.id, row.wl_id));
      } catch (err) {
        logExternalError("error", { service: "database", operation: "touch_watchlist_access" }, err);
      }
    });
  }

  return {
    id: row.wl_id,
    slug: row.slug,
    title: row.title,
    description: row.description,
    isPublic: row.is_public,
    alertsEnabled: row.alerts_enabled,
    filters: normalizeWatchlistFiltersForRead(row.filters),
    sourceWatchlistId: row.source_watchlist_id,
    createdAt: new Date(row.created_at).toISOString(),
    owner: {
      id: row.owner_id,
      username: row.username,
      displayUsername: row.display_username,
      name: row.owner_name,
    },
    companies: normalizeWatchlistCompaniesForRead(row.companies) ?? [],
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
                  'id', bounded_company.id,
                  'name', bounded_company.name,
                  'slug', bounded_company.slug,
                  'icon', bounded_company.icon
                )
                ORDER BY bounded_company.name
              )
              FROM (
                SELECT c.id, c.name, c.slug, c.icon
                FROM watchlist_company wc
                JOIN company c ON c.id = wc.company_id
                WHERE wc.watchlist_id = w.id
                ORDER BY c.name
                LIMIT ${WATCHLIST_COMPANY_MAX}
              ) bounded_company
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
    filters: normalizeWatchlistFiltersForRead(row.filters),
    sourceWatchlistId: row.source_watchlist_id,
    createdAt: new Date(row.created_at).toISOString(),
    owner: {
      id: row.owner_id,
      username: row.username,
      displayUsername: row.display_username,
      name: row.owner_name,
    },
    companies: normalizeWatchlistCompaniesForRead(row.companies) ?? [],
  };
}

export type PublicWatchlistEntry = WatchlistSummary & {
  ownerName: string;
  ownerUsername: string | null;
  mirrorCount: number;
};

type InternalPublicWatchlistEntry = PublicWatchlistEntry & {
  indexedFilters: IndexedWatchlistFilters | null;
  indexedFilterCacheKey: string | null;
  companyIds: string[] | null;
};

type ListingCountCandidate = {
  id: string;
  filters: IndexedWatchlistFilters;
  companyIds: string[];
  fallbackCount: number;
};

type ListingCountSearch = {
  collection: "job_posting";
  q: string;
  query_by: "title";
  filter_by: string;
  per_page: 0;
};

type ListingActivitySearch = ListingCountSearch & {
  facet_by: "company_id";
  facet_strategy: "exhaustive";
  max_facet_values: 4;
};

type ListingActivityAggregate = {
  activeJobCount: number;
  activeCompanyCount: number;
  companyCounts: Map<string, number>;
};

/** Stable cache-key fragment for a viewer's language filter. */
function languagesCacheKey(languages: string[] | undefined): string {
  if (!languages || languages.length === 0) return "all";
  return languages.join(",");
}

function stringList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const result = value.filter((item): item is string => typeof item === "string" && item.length > 0);
  return result.length > 0 ? result : undefined;
}

function workModeList(value: unknown): WorkMode[] | undefined {
  const result = stringList(value)?.filter((item): item is WorkMode =>
    WORK_MODE_VALUES.has(item as WorkMode),
  );
  return result && result.length > 0 ? result : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numberList(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const seen = new Set<number>();
  const result: number[] = [];
  for (const item of value) {
    if (typeof item !== "number" || !Number.isInteger(item) || item < 0 || seen.has(item)) continue;
    seen.add(item);
    result.push(item);
  }
  return result.length > 0 ? result : undefined;
}

function buildIndexedWatchlistFiltersPayload(
  filters: WatchlistFilters | IndexedWatchlistFilters | null | undefined,
  ids?: Pick<
    IndexedWatchlistFilters,
    "locationIds" | "occupationIds" | "seniorityIds" | "technologyIds"
  >,
): IndexedWatchlistFilters {
  const f = (filters ?? {}) as Record<string, unknown>;
  const payload: IndexedWatchlistFilters = {};

  if (f.anyCompany === true) payload.anyCompany = true;

  const keywords = stringList(f.keywords);
  if (keywords) payload.keywords = keywords;
  const locationSlugs = stringList(f.locationSlugs);
  if (locationSlugs) payload.locationSlugs = locationSlugs;
  const occupationSlugs = stringList(f.occupationSlugs);
  if (occupationSlugs) payload.occupationSlugs = occupationSlugs;
  const senioritySlugs = stringList(f.senioritySlugs);
  const rawEmploymentType = stringList(f.employmentType);
  const employmentType = rawEmploymentType?.filter(
    (value) => value.toLowerCase() !== "internship",
  );
  const hadLegacyInternship = Boolean(
    rawEmploymentType?.some(
      (value) => value.toLowerCase() === "internship",
    ),
  );
  if (senioritySlugs) payload.senioritySlugs = senioritySlugs;
  else if (hadLegacyInternship && !employmentType?.length) {
    payload.senioritySlugs = ["intern"];
  }
  const technologySlugs = stringList(f.technologySlugs);
  if (technologySlugs) payload.technologySlugs = technologySlugs;
  const workMode = workModeList(f.workMode);
  if (workMode) payload.workMode = workMode;
  if (employmentType && employmentType.length > 0) {
    payload.employmentType = employmentType;
  }
  if (typeof f.salaryCurrency === "string" && f.salaryCurrency.length > 0) {
    payload.salaryCurrency = f.salaryCurrency;
  }

  const salaryMin = numberValue(f.salaryMin);
  if (salaryMin !== undefined) payload.salaryMin = salaryMin;
  const salaryMax = numberValue(f.salaryMax);
  if (salaryMax !== undefined) payload.salaryMax = salaryMax;
  const experienceMin = numberValue(f.experienceMin);
  if (experienceMin !== undefined) payload.experienceMin = experienceMin;
  const experienceMax = numberValue(f.experienceMax);
  if (experienceMax !== undefined) payload.experienceMax = experienceMax;

  const locationIds = numberList(ids?.locationIds ?? f.locationIds);
  if (locationIds) payload.locationIds = locationIds;
  const occupationIds = numberList(ids?.occupationIds ?? f.occupationIds);
  if (occupationIds) payload.occupationIds = occupationIds;
  const seniorityIds = numberList(ids?.seniorityIds ?? f.seniorityIds);
  if (seniorityIds) payload.seniorityIds = seniorityIds;
  const technologyIds = numberList(ids?.technologyIds ?? f.technologyIds);
  if (technologyIds) payload.technologyIds = technologyIds;

  return payload;
}

function parseIndexedWatchlistFilters(raw: unknown): IndexedWatchlistFilters | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return null;
    }
    return buildIndexedWatchlistFiltersPayload(parsed as IndexedWatchlistFilters);
  } catch {
    return null;
  }
}

function hasUnresolvedIndexedTaxonomy(filters: IndexedWatchlistFilters): boolean {
  return Boolean(
    (filters.locationSlugs?.length && (filters.locationIds?.length ?? 0) < filters.locationSlugs.length) ||
      (filters.occupationSlugs?.length && (filters.occupationIds?.length ?? 0) < filters.occupationSlugs.length) ||
      (filters.senioritySlugs?.length && (filters.seniorityIds?.length ?? 0) < filters.senioritySlugs.length) ||
      (filters.technologySlugs?.length && (filters.technologyIds?.length ?? 0) < filters.technologySlugs.length),
  );
}

function pgTextArrayLiteral(values: string[]): string {
  const escaped = values.map((value) =>
    `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`,
  );
  return `{${escaped.join(",")}}`;
}

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
      logExternalError("error", { service: "typesense", operation: "watchlist_company_count" }, err);
      return 0;
    }
    // Aligned to the watchlist-detail ISR window (1h, see page.tsx). Bumped
    // from 600s with #2648 — metadata freshness from a viewer's perspective
    // comes from the client-hydrated body, not the cached count.
  }, { ttl: CACHE_TTL_LONG });
}

/**
 * Patch `active_job_count` on Discover-surface entries whose filters make
 * the denormalized company-scope count inaccurate (#3261).
 *
 * Typesense watchlist hits carry sanitized `filters_json` with resolved
 * taxonomy IDs. They do not carry company IDs, so filtered company-scoped
 * rows hydrate only those IDs from Postgres in one batch before issuing a
 * single `job_posting` multi_search. Any-company rows need no company
 * hydration. Unfiltered company-scoped rows keep the denormalized doc
 * count and pay no extra I/O.
 *
 * Failures or missing resolved taxonomy IDs degrade to the existing
 * denormalized count; missing IDs are never broadened into a less
 * selective query.
 */
async function _patchPreciseCountsForDiscover(
  entries: InternalPublicWatchlistEntry[],
  languages: string[],
): Promise<InternalPublicWatchlistEntry[]> {
  if (entries.length === 0) return entries;

  const filteredEntries = entries.filter((e) =>
    e.indexedFilters &&
    hasPreciseListingCountFilters(e.indexedFilters) &&
    !hasUnresolvedIndexedTaxonomy(e.indexedFilters),
  );
  if (filteredEntries.length === 0) return entries;

  const companyScopedEntries = filteredEntries.filter((e) =>
    !e.indexedFilters!.anyCompany &&
    e.companyIds === null,
  );
  let hydratedCompanyIds = new Map<string, string[]>();
  if (companyScopedEntries.length > 0) {
    try {
      hydratedCompanyIds = await _fetchWatchlistCompanyIds(companyScopedEntries.map((e) => e.id));
    } catch (err) {
      logExternalError("error", { service: "database", operation: "hydrate_watchlist_company_ids" }, err);
      return entries;
    }
  }

  const candidates: ListingCountCandidate[] = filteredEntries.map((entry) => ({
    id: entry.id,
    filters: entry.indexedFilters!,
    companyIds: entry.indexedFilters!.anyCompany
      ? []
      : (entry.companyIds ?? hydratedCompanyIds.get(entry.id) ?? []),
    fallbackCount: entry.activeJobCount,
  }));
  const patchedCounts = await resolvePreciseListingCounts(
    candidates,
    languages,
    "_patchPreciseCountsForDiscover",
  );

  return entries.map((e) =>
    patchedCounts.has(e.id)
      ? { ...e, activeJobCount: patchedCounts.get(e.id)! }
      : e,
  );
}

async function _fetchWatchlistCompanyIds(watchlistIds: string[]): Promise<Map<string, string[]>> {
  if (watchlistIds.length === 0) return new Map();
  const pgArray = pgTextArrayLiteral([...new Set(watchlistIds)]);
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        watchlist_id: string;
        company_ids: string[];
      }>(sql`
        SELECT wc.watchlist_id::text AS watchlist_id,
               COALESCE(array_agg(wc.company_id::text ORDER BY wc.company_id::text), ARRAY[]::text[]) AS company_ids
        FROM watchlist_company wc
        WHERE wc.watchlist_id = ANY(${pgArray}::uuid[])
        GROUP BY wc.watchlist_id
      `),
    { label: "watchlistCompanyIdsForListingCounts" },
  );

  const result = new Map<string, string[]>();
  for (const row of rows as unknown as { watchlist_id: string; company_ids: string[] }[]) {
    result.set(row.watchlist_id, row.company_ids ?? []);
  }
  return result;
}

export async function searchPublicWatchlists(params: {
  query: string;
  offset: number;
  limit: number;
  locale: string;
  failOnUnavailable?: boolean;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const q = params.query.trim();
  if (!q) return { watchlists: [], total: 0 };

  const languages = await getViewerLanguages(params.locale);
  const langKey = languagesCacheKey(languages);

  try {
    return await cached(
      `public-watchlist-search:${q}:${params.offset}:${params.limit}:${langKey}`,
      async () => {
        const tsResult = await _searchPublicWatchlistsTypesense(q, params.offset, params.limit);
        // Perf (#3176/#3492): unfiltered company-scoped cards trust
        // the denormalized `active_job_count` carried on each
        // Typesense `watchlist` doc. Filtered or `anyCompany` cards
        // use the self-contained `filters_json` payload plus one
        // batched `job_posting` multi_search for precise counts
        // (#3261).
        const patched = await _patchPreciseCountsForDiscover(tsResult.watchlists, languages);
        return {
          watchlists: stripIndexedWatchlistFields(patched),
          total: tsResult.total,
        };
      },
      { ttl: CACHE_TTL_SHORT },
    );
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    logExternalError("error", { service: "typesense", operation: "search_public_watchlists" }, err);
    return { watchlists: [], total: 0 };
  }
}

export async function getPopularWatchlists(params: {
  offset: number;
  limit: number;
  locale: string;
  failOnUnavailable?: boolean;
}): Promise<{ watchlists: PublicWatchlistEntry[]; total: number }> {
  const languages = await getViewerLanguages(params.locale);
  const langKey = languagesCacheKey(languages);

  try {
    return await cached(
      `popular-watchlists:${params.offset}:${params.limit}:${langKey}`,
      async () => {
        const tsResult = await _getPopularWatchlistsTypesense(params.offset, params.limit);
        // Use the same count semantics as `searchPublicWatchlists`:
        // denormalized for unfiltered company-scoped rows, batched
        // precise counts for filtered or `anyCompany` rows (#3261).
        const patched = await _patchPreciseCountsForDiscover(tsResult.watchlists, languages);
        return {
          watchlists: stripIndexedWatchlistFields(patched),
          total: tsResult.total,
        };
      },
      { ttl: CACHE_TTL_POPULAR },
    );
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    logExternalError("error", { service: "typesense", operation: "popular_watchlists" }, err);
    return { watchlists: [], total: 0 };
  }
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
    const result = await readWatchlistCandidates({
      filters: params,
      offset: params.offset,
      limit: params.limit,
      abortSignal: params.abortSignal,
    });
    return {
      ...result,
      ...(!userId && params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS
        ? { truncated: true }
        : {}),
    };
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    logExternalError("error", { service: "typesense", operation: "watchlist_postings" }, err);
    return {
      postings: [],
      total: 0,
      ...(!userId && params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS
        ? { truncated: true }
        : {}),
    };
  }
}

/**
 * Session-free counterpart used by cached public-watchlist snapshots.
 *
 * `getWatchlistPostings` reads `getSessionUserId()` to apply saved-job state
 * and anonymous truncation. That request API is intentionally forbidden
 * inside a shared `"use cache"` boundary, so the public route must pass the
 * known-anonymous viewer explicitly instead of consulting `headers()`.
 */
export async function getPublicWatchlistPostings(
  params: WatchlistPostingQueryParams,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  if (!params.anyCompany && params.companyIds.length === 0) {
    return { postings: [], total: 0 };
  }

  if (params.offset >= ANON_MAX_WATCHLIST_POSTINGS) {
    return { postings: [], total: 0, truncated: true };
  }

  try {
    const result = await readWatchlistCandidates({
      filters: params,
      offset: params.offset,
      limit: params.limit,
      abortSignal: params.abortSignal,
    });
    return {
      ...result,
      ...(params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS
        ? { truncated: true }
        : {}),
    };
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    logExternalError("error", { service: "typesense", operation: "public_watchlist_postings" }, err);
    return {
      postings: [],
      total: 0,
      ...(params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS
        ? { truncated: true }
        : {}),
    };
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
    const baseParts = [POSTING_FLOW_FILTER, `first_seen_at:>${oneYearAgo}`];
    const buildSearchParams = (companyIds: readonly string[]) => ({
      q: keywordsQ,
      query_by: "title",
      filter_by: buildWatchlistPostingFilter(baseParts, companyIds, filterStr),
      per_page: 0,
    });
    const batches = params.companyIds.length > 0
      ? splitValuesForTypesenseQuery(params.companyIds, buildSearchParams, COMPANY_BATCH_SIZE)
      : [[]];
    const results = await Promise.all(
      batches.map((batch) =>
        withTypesenseRetry(
          () =>
            client.collections("job_posting").documents().search(
              buildSearchParams(batch),
              { abortSignal: params.abortSignal },
            ),
          {
            label: "getWatchlistPostingYearCount",
            abortSignal: params.abortSignal,
          },
        ),
      ),
    );
    for (const result of results) assertTypesenseSearchResult(result);
    return results.reduce((sum, result) => sum + (result.found ?? 0), 0);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    logExternalError("error", { service: "typesense", operation: "watchlist_posting_year_count" }, err);
    return 0;
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
    workMode: workModeList(f.workMode),
    employmentTypes: f.employmentType?.length ? f.employmentType : undefined,
    salaryMinEur: f.salaryMin,
    salaryMaxEur: f.salaryMax,
    experienceMin: f.experienceMin,
    experienceMax: f.experienceMax,
  });
  const hasKeywords = f.keywords && f.keywords.length > 0;
  const q = hasKeywords ? f.keywords!.join(" ") : "*";

  const oneYearAgo = Math.floor((Date.now() - 365 * 24 * 3600 * 1000) / 1000);

  try {
    const client = getSearchClient();
    const searchCount = async (baseParts: string[], label: string): Promise<number> => {
      const buildSearchParams = (batch: readonly string[]) => ({
        q,
        query_by: "title",
        filter_by: buildWatchlistPostingFilter(baseParts, !isAny ? batch : [], filterStr),
        per_page: 0,
      });
      const batches = !isAny && companyIds.length > 0
        ? splitValuesForTypesenseQuery(companyIds, buildSearchParams, COMPANY_BATCH_SIZE)
        : [[]];
      const results = await Promise.all(
        batches.map((batch) =>
          withTypesenseRetry(
            () => client.collections("job_posting").documents().search(buildSearchParams(batch)),
            { label },
          ),
        ),
      );
      return results.reduce((sum, result) => sum + (result.found ?? 0), 0);
    };
    // Mirror `POSTING_FLOW_FILTER` (#2965) on the year filter so the
    // year-count stays content-quality-consistent with the active filter
    // (which already includes `has_content:!=false` via POSTING_BASE_FILTER).
    // See issue #3029.
    const [activeJobs, yearJobs] = await Promise.all([
      searchCount([POSTING_BASE_FILTER], "getWatchlistPostingDisplayCounts.active"),
      searchCount([POSTING_FLOW_FILTER, `first_seen_at:>${oneYearAgo}`], "getWatchlistPostingDisplayCounts.year"),
    ]);
    return {
      activeJobs,
      yearJobs,
    };
  } catch (err) {
    logExternalError("error", { service: "typesense", operation: "watchlist_display_counts" }, err);
    return { activeJobs: 0, yearJobs: 0 };
  }
}

export async function addCompanyToWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<{ ok: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  const normalizedCompanyId = normalizeWatchlistUuid(companyId);
  if (!normalizedWatchlistId || !normalizedCompanyId) return { ok: false };
  watchlistId = normalizedWatchlistId;
  companyId = normalizedCompanyId;

  // Serialize membership growth on the parent row. Checking the bounded
  // membership set while holding this lock prevents two concurrent adds from
  // both observing slot 250 as available.
  const mutation = await db.transaction(async (tx) => {
    const [owned] = await tx
      .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
      .from(watchlist)
      .where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, userId)))
      .for("update")
      .limit(1);
    if (!owned) return null;

    const existing = await tx
      .select({ companyId: watchlistCompany.companyId })
      .from(watchlistCompany)
      .where(eq(watchlistCompany.watchlistId, watchlistId))
      .limit(WATCHLIST_COMPANY_MAX);
    if (existing.some((row) => row.companyId === companyId)) {
      return { wl: owned, changed: false };
    }
    if (existing.length >= WATCHLIST_COMPANY_MAX) return null;

    await tx
      .insert(watchlistCompany)
      .values({ watchlistId, companyId })
      .onConflictDoNothing();
    return { wl: owned, changed: true };
  });

  if (!mutation) return { ok: false };
  if (!mutation.changed) return { ok: true };
  const wl = mutation.wl;

  _logWatchlistAudit({
    action: "watchlist.company.add",
    userId,
    watchlist_id: watchlistId,
    slug_before: wl.slug,
    slug_after: wl.slug,
    is_public_before: wl.isPublic,
    is_public_after: wl.isPublic,
    company_count_delta: 1,
  });

  // The companies array drives the cached page's JSON-LD ItemList,
  // metadata description ("Jobs at X, Y, Z"), and OG image. Bust the
  // page cache + Redis layer so the change is visible on the next read.
  if (wl.isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [wl.slug]);
        await _unindexPublicWatchlist(userId, watchlistId, [wl.slug], "addWatchlistCompany");
      } catch (err) {
        logExternalError("error", { service: "typesense", operation: "add_watchlist_company_hook" }, err);
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
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  if (!normalizedWatchlistId) return { ok: false };
  watchlistId = normalizedWatchlistId;

  const [wl] = await db
    .select({ userId: watchlist.userId, slug: watchlist.slug, isPublic: watchlist.isPublic })
    .from(watchlist)
    .where(eq(watchlist.id, watchlistId))
    .limit(1);

  if (!wl || wl.userId !== userId) return { ok: false };

  await db
    .delete(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));

  _logWatchlistAudit({
    action: "watchlist.companies.clear",
    userId,
    watchlist_id: watchlistId,
    slug_before: wl.slug,
    slug_after: wl.slug,
    is_public_before: wl.isPublic,
    is_public_after: wl.isPublic,
    company_count_delta: null,
  });

  if (wl.isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [wl.slug]);
        await _unindexPublicWatchlist(userId, watchlistId, [wl.slug], "clearWatchlistCompanies");
      } catch (err) {
        logExternalError("error", { service: "typesense", operation: "clear_watchlist_companies_hook" }, err);
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
  const normalizedWatchlistId = normalizeWatchlistUuid(watchlistId);
  const normalizedCompanyId = normalizeWatchlistUuid(companyId);
  if (!normalizedWatchlistId || !normalizedCompanyId) return { ok: false };
  watchlistId = normalizedWatchlistId;
  companyId = normalizedCompanyId;

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

  _logWatchlistAudit({
    action: "watchlist.company.remove",
    userId,
    watchlist_id: watchlistId,
    slug_before: wl.slug,
    slug_after: wl.slug,
    is_public_before: wl.isPublic,
    is_public_after: wl.isPublic,
    company_count_delta: -1,
  });

  if (wl.isPublic) {
    after(async () => {
      try {
        await _invalidateWatchlistCaches(userId, [wl.slug]);
        await _unindexPublicWatchlist(userId, watchlistId, [wl.slug], "removeWatchlistCompany");
      } catch (err) {
        logExternalError("error", { service: "typesense", operation: "remove_watchlist_company_hook" }, err);
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
): Promise<{ watchlists: InternalPublicWatchlistEntry[]; total: number }> {
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
): Promise<{ watchlists: InternalPublicWatchlistEntry[]; total: number }> {
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

function _mapWatchlistDoc(doc: Record<string, unknown>): InternalPublicWatchlistEntry {
  const createdAtTs = doc.created_at as number;
  const rawFiltersJson = doc.filters_json;
  const indexedFilters = parseIndexedWatchlistFilters(rawFiltersJson);
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
    indexedFilters,
    indexedFilterCacheKey: typeof rawFiltersJson === "string" && rawFiltersJson.length > 0
      ? rawFiltersJson
      : null,
    companyIds: null,
  };
}

function stripIndexedWatchlistFields(
  entries: InternalPublicWatchlistEntry[],
): PublicWatchlistEntry[] {
  return entries.map(({
    indexedFilters: _indexedFilters,
    indexedFilterCacheKey: _key,
    companyIds: _companyIds,
    ...entry
  }) => entry);
}

function buildWatchlistPostingFilter(
  baseParts: readonly string[],
  companyIds: readonly string[],
  filterStr: string,
): string {
  return [
    ...baseParts,
    companyIds.length > 0 ? `company_id:[${companyIds.join(",")}]` : "",
    filterStr,
  ].filter(Boolean).join(" && ");
}

// ── Helper functions for Typesense write hooks ────────────────────────

type WatchlistOwnerInfo = {
  name: string;
  username: string | null;
  displayUsername: string | null;
};

async function _unindexPublicWatchlist(
  userId: string,
  watchlistId: string,
  slugsToNotify: string[],
  logLabel: string,
): Promise<void> {
  tsDeleteWatchlist(watchlistId);
  if (slugsToNotify.length === 0) return;

  const owner = await _getOwnerInfo(userId);
  if (!owner) return;
  await _notifyWatchlistUrls(owner, slugsToNotify, logLabel);
}

async function _notifyWatchlistUrls(
  owner: WatchlistOwnerInfo,
  slugs: string[],
  logLabel: string,
): Promise<void> {
  const urls = _watchlistIndexNowUrls(owner, slugs);
  if (urls.length === 0) return;

  const result = await notifyIndexNow(urls);
  logIndexNowResult(logLabel, result);
}

function _watchlistIndexNowUrls(
  owner: WatchlistOwnerInfo,
  slugs: string[],
): string[] {
  // Match sitemap semantics: only notify URLs the sitemap also exposes
  // (see apps/web/src/lib/sitemap.ts — filters `u.username IS NOT NULL`).
  if (!owner.username) return [];

  const userSlug = owner.displayUsername ?? owner.username;
  return [...new Set(slugs)].map((slug) => `/${userSlug}/${slug}`);
}

/**
 * Invalidate every cache layer that could be holding a public watchlist's
 * pre-mutation state: the per-region `'use cache'` page entry (tagged via
 * `watchlistCacheTag`), the Redis-backed `cached("public-watchlist:...")`
 * SQL fetch, AND the proxy's public-route status cache. Required for both
 * privacy toggles AND title renames AND
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
        await Promise.all([
          invalidate(`public-watchlist:${userSlug}:${slug}`),
          invalidate(publicWatchlistRouteStatusCacheKey(userSlug, slug)),
        ]);
      } catch (err) {
        logExternalError("error", { service: "redis", operation: "invalidate_watchlist_caches" }, err);
      }
    }
  }
}

/** Fetch owner info for Typesense watchlist doc + IndexNow URL construction. */
async function _getOwnerInfo(
  userId: string,
): Promise<WatchlistOwnerInfo | null> {
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
