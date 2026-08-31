import "server-only";

import {
  and,
  asc,
  eq,
  inArray,
  isNotNull,
  lt,
  lte,
  notInArray,
  or,
  sql,
} from "drizzle-orm";

import { db } from "@/db";
import {
  notificationDelivery,
  user,
  userPreferences,
  watchlist,
  watchlistCompany,
} from "@/db/schema";
import type {
  NotificationCadence,
  NotificationDeliveryStatus,
} from "@/lib/notifications/contracts";
import { MAX_NOTIFICATION_PROVIDER_ATTEMPTS } from "@/lib/notifications/policy";
import {
  runNotificationSchedulerCore,
  type EligibleNotificationUser,
  type EligibleNotificationWatchlist,
  type NotificationDeliveryClaim,
  type NotificationMatchSummary,
  type NotificationSchedulerRepository,
} from "@/lib/notifications/scheduler-core";
import {
  NOTIFICATION_CLAIM_LEASE_MS,
  NOTIFICATION_MATCH_LIMIT_PER_WATCHLIST,
  type NotificationExecutionMode,
  type NotificationQuotaState,
  type UtcWindow,
} from "@/lib/notifications/scheduler-policy";
import { canonicalStringCompare } from "@/lib/sort";
import {
  compileWatchlistMatcherSources,
  matchCompiledWatchlistsInWindow,
} from "@/lib/services/watchlist-matcher";
import type {
  MatchedWatchlistPosting,
  WatchlistFilters,
} from "@/lib/watchlist-matcher-contract";

const COMPLETED_STATUSES: NotificationDeliveryStatus[] = ["sent", "skipped"];

function identityKey(userId: string, cadence: NotificationCadence): string {
  return `${userId}\u0000${cadence}`;
}

async function listEligibleUsers(): Promise<readonly EligibleNotificationUser[]> {
  const rows = await db
    .select({
      userId: user.id,
      cadence: userPreferences.notificationCadence,
      notificationsStateChangedAt:
        userPreferences.notificationsStateChangedAt,
      locale: userPreferences.locale,
      jobLanguages: userPreferences.jobLanguages,
      watchlistId: watchlist.id,
      watchlistLabel: watchlist.title,
      filters: watchlist.filters,
      alertsEnabledAt: watchlist.alertsEnabledAt,
      companyId: watchlistCompany.companyId,
    })
    .from(user)
    .innerJoin(userPreferences, eq(userPreferences.userId, user.id))
    .innerJoin(watchlist, eq(watchlist.userId, user.id))
    .leftJoin(watchlistCompany, eq(watchlistCompany.watchlistId, watchlist.id))
    .where(and(
      eq(user.emailVerified, true),
      eq(userPreferences.notificationsPaused, false),
      eq(watchlist.alertsEnabled, true),
      isNotNull(watchlist.alertsEnabledAt),
    ))
    .orderBy(asc(user.id), asc(watchlist.id), asc(watchlistCompany.companyId));

  const userIds = [...new Set(rows.map((row) => row.userId))];
  if (userIds.length === 0) return [];
  const [completedRows, openRows] = await Promise.all([
    db
      .select({
        userId: notificationDelivery.userId,
        cadence: notificationDelivery.cadence,
        windowEnd: sql<Date>`max(${notificationDelivery.windowEnd})`,
      })
      .from(notificationDelivery)
      .where(and(
        inArray(notificationDelivery.userId, userIds),
        inArray(notificationDelivery.status, COMPLETED_STATUSES),
      ))
      .groupBy(notificationDelivery.userId, notificationDelivery.cadence),
    db
      .select({
        userId: notificationDelivery.userId,
        cadence: notificationDelivery.cadence,
        scheduledFor: notificationDelivery.scheduledFor,
        windowStart: notificationDelivery.windowStart,
        windowEnd: notificationDelivery.windowEnd,
        status: notificationDelivery.status,
      })
      .from(notificationDelivery)
      .where(and(
        inArray(notificationDelivery.userId, userIds),
        notInArray(notificationDelivery.status, COMPLETED_STATUSES),
      ))
      .orderBy(asc(notificationDelivery.scheduledFor)),
  ]);

  const completed = new Map(
    completedRows.map((row) => [identityKey(row.userId, row.cadence), row.windowEnd]),
  );
  const open = new Map<string, (typeof openRows)[number]>();
  for (const row of openRows) {
    const key = identityKey(row.userId, row.cadence);
    const current = open.get(key);
    // Any ambiguous provider outcome blocks later work even if an inconsistent
    // historical dataset contains more than one unresolved row.
    if (!current || (row.status === "unknown" && current.status !== "unknown")) {
      open.set(key, row);
    }
  }

  type MutableUser = {
    userId: string;
    cadence: NotificationCadence;
    notificationsStateChangedAt: Date;
    watchlists: Map<string, {
      source: EligibleNotificationWatchlist["source"];
      alertsEnabledAt: Date;
      companyIds: Set<string>;
    }>;
  };
  const grouped = new Map<string, MutableUser>();
  for (const row of rows) {
    if (!row.alertsEnabledAt) continue;
    const key = identityKey(row.userId, row.cadence);
    let owner = grouped.get(key);
    if (!owner) {
      owner = {
        userId: row.userId,
        cadence: row.cadence,
        notificationsStateChangedAt: row.notificationsStateChangedAt,
        watchlists: new Map(),
      };
      grouped.set(key, owner);
    }
    let entry = owner.watchlists.get(row.watchlistId);
    if (!entry) {
      entry = {
        source: {
          watchlistId: row.watchlistId,
          watchlistLabel: row.watchlistLabel,
          filters: row.filters as WatchlistFilters,
          companyIds: [],
          locale: row.locale,
          jobLanguages: row.jobLanguages,
        },
        alertsEnabledAt: row.alertsEnabledAt,
        companyIds: new Set(),
      };
      owner.watchlists.set(row.watchlistId, entry);
    }
    if (row.companyId) entry.companyIds.add(row.companyId);
  }

  return [...grouped.entries()].map(([key, owner]) => ({
    userId: owner.userId,
    cadence: owner.cadence,
    notificationsStateChangedAt: owner.notificationsStateChangedAt,
    lastProcessedWindowEnd: completed.get(key) ?? null,
    openDelivery: open.has(key)
      ? {
          scheduledFor: open.get(key)!.scheduledFor,
          windowStart: open.get(key)!.windowStart,
          windowEnd: open.get(key)!.windowEnd,
          status: open.get(key)!.status as Exclude<
            NotificationDeliveryStatus,
            "sent" | "skipped"
          >,
        }
      : null,
    watchlists: [...owner.watchlists.values()].map((entry) => ({
      alertsEnabledAt: entry.alertsEnabledAt,
      source: { ...entry.source, companyIds: [...entry.companyIds] },
    })),
  }));
}

async function claim(input: {
  userId: string;
  cadence: NotificationCadence;
  scheduledFor: Date;
  windowStart: Date;
  windowEnd: Date;
  idempotencyKey: string;
  now: Date;
}) {
  const [inserted] = await db
    .insert(notificationDelivery)
    .values({
      userId: input.userId,
      cadence: input.cadence,
      scheduledFor: input.scheduledFor,
      windowStart: input.windowStart,
      windowEnd: input.windowEnd,
      idempotencyKey: input.idempotencyKey,
      status: "pending",
      createdAt: input.now,
      updatedAt: input.now,
    })
    .onConflictDoNothing()
    .returning({ id: notificationDelivery.id });
  if (inserted) {
    return {
      kind: "claimed" as const,
      claim: { id: inserted.id, leaseAcquiredAt: input.now },
    };
  }

  const leaseExpiredAt = new Date(input.now.getTime() - NOTIFICATION_CLAIM_LEASE_MS);
  const [reclaimed] = await db
    .update(notificationDelivery)
    .set({
      status: "pending",
      windowStart: input.windowStart,
      windowEnd: input.windowEnd,
      matchCount: null,
      deferredUntil: null,
      lastErrorCode: null,
      updatedAt: input.now,
    })
    .where(and(
      eq(notificationDelivery.userId, input.userId),
      eq(notificationDelivery.cadence, input.cadence),
      eq(notificationDelivery.scheduledFor, input.scheduledFor),
      or(
        and(
          eq(notificationDelivery.status, "pending"),
          lte(notificationDelivery.updatedAt, leaseExpiredAt),
        ),
        and(
          eq(notificationDelivery.status, "failed"),
          lt(
            notificationDelivery.providerAttemptCount,
            MAX_NOTIFICATION_PROVIDER_ATTEMPTS,
          ),
        ),
        and(
          eq(notificationDelivery.status, "quota_deferred"),
          lte(notificationDelivery.deferredUntil, input.now),
        ),
      ),
    ))
    .returning({ id: notificationDelivery.id });
  if (reclaimed) {
    return {
      kind: "claimed" as const,
      claim: { id: reclaimed.id, leaseAcquiredAt: input.now },
    };
  }
  const [existing] = await db
    .select({ status: notificationDelivery.status })
    .from(notificationDelivery)
    .where(and(
      eq(notificationDelivery.userId, input.userId),
      eq(notificationDelivery.cadence, input.cadence),
      eq(notificationDelivery.scheduledFor, input.scheduledFor),
    ))
    .limit(1);
  if (!existing) throw new Error("Notification idempotency key collision");
  return { kind: "duplicate" as const, status: existing.status };
}

function pendingLease(claim: NotificationDeliveryClaim) {
  return and(
    eq(notificationDelivery.id, claim.id),
    eq(notificationDelivery.status, "pending"),
    eq(notificationDelivery.updatedAt, claim.leaseAcquiredAt),
  );
}

const repository: NotificationSchedulerRepository = {
  listEligibleUsers,
  claim,
  async markSkipped(input) {
    const rows = await db.update(notificationDelivery).set({
      status: "skipped",
      matchCount: 0,
      completedAt: input.completedAt,
      updatedAt: input.completedAt,
    }).where(pendingLease(input.claim)).returning({ id: notificationDelivery.id });
    return rows.length === 1;
  },
  async markFailed(input) {
    const rows = await db.update(notificationDelivery).set({
      status: "failed",
      lastErrorCode: input.errorCode,
      updatedAt: input.failedAt,
    }).where(pendingLease(input.claim)).returning({ id: notificationDelivery.id });
    return rows.length === 1;
  },
  async markQuotaDeferred(input) {
    const rows = await db.update(notificationDelivery).set({
      status: "quota_deferred",
      matchCount: input.matchCount,
      deferredUntil: input.deferredUntil,
      updatedAt: input.deferredAt,
    }).where(pendingLease(input.claim)).returning({ id: notificationDelivery.id });
    return rows.length === 1;
  },
  async recordShadowPlan(input) {
    const rows = await db.update(notificationDelivery).set({
      matchCount: input.matchCount,
      updatedAt: input.plannedAt,
    }).where(pendingLease(input.claim)).returning({ id: notificationDelivery.id });
    return rows.length === 1;
  },
};

async function match(input: {
  watchlists: readonly (EligibleNotificationWatchlist & { windowStart: Date })[];
  windowEnd: Date;
}): Promise<NotificationMatchSummary> {
  const compiled = await compileWatchlistMatcherSources(
    input.watchlists.map((entry) => entry.source),
  );
  const compiledById = new Map(compiled.map((entry) => [entry.watchlistId, entry]));
  const groups = new Map<string, typeof compiled>();
  for (const entry of input.watchlists) {
    const key = entry.windowStart.toISOString();
    const group = groups.get(key) ?? [];
    group.push(compiledById.get(entry.source.watchlistId)!);
    groups.set(key, group);
  }

  const postings = new Map<string, MatchedWatchlistPosting>();
  let watchlistMatchCount = 0;
  let truncated = false;
  for (const [windowStart, watchlists] of groups) {
    const result = await matchCompiledWatchlistsInWindow({
      watchlists,
      windowStart: new Date(windowStart),
      windowEnd: input.windowEnd,
      limitPerWatchlist: NOTIFICATION_MATCH_LIMIT_PER_WATCHLIST,
    });
    for (const stats of result.watchlists) {
      watchlistMatchCount += stats.total;
      truncated ||= stats.truncated;
    }
    for (const posting of result.postings) {
      const existing = postings.get(posting.id);
      if (!existing) {
        postings.set(posting.id, {
          ...posting,
          matchedWatchlists: [...posting.matchedWatchlists],
        });
        continue;
      }
      const seen = new Set(existing.matchedWatchlists.map((label) => label.id));
      existing.matchedWatchlists.push(
        ...posting.matchedWatchlists.filter((label) => !seen.has(label.id)),
      );
    }
  }
  const ordered = [...postings.values()].sort((left, right) =>
    Date.parse(right.firstSeenAt) - Date.parse(left.firstSeenAt) ||
    canonicalStringCompare(left.id, right.id),
  );
  return {
    postings: ordered,
    uniqueMatchCount: ordered.length,
    watchlistMatchCount,
    truncated,
  };
}

/** Providerless entry point; no route or runtime configuration invokes it. */
export async function runNotificationScheduler(input: {
  mode?: NotificationExecutionMode;
  sweep: UtcWindow;
  quota: NotificationQuotaState;
  concurrency: number;
}) {
  return runNotificationSchedulerCore(input, { repository, match });
}
