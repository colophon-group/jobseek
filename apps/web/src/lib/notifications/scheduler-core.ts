import "server-only";

import type {
  NotificationCadence,
  NotificationDeliveryStatus,
} from "./contracts";
import {
  createNotificationDeliveryIdempotencyKey,
  getNotificationWindowFloor,
} from "./policy";
import {
  DEFAULT_NOTIFICATION_EXECUTION_MODE,
  NOTIFICATION_DISPLAY_ITEM_LIMIT,
  assertUtcWindow,
  calculateNotificationQuota,
  getNotificationScheduleSlots,
  mapWithConcurrency,
  type NotificationExecutionMode,
  type NotificationQuotaState,
  type UtcWindow,
} from "./scheduler-policy";
import type {
  MatchedWatchlistPosting,
  WatchlistMatcherSource,
} from "@/lib/watchlist-matcher-contract";

export type EligibleNotificationWatchlist = Readonly<{
  source: WatchlistMatcherSource;
  alertsEnabledAt: Date;
}>;

export type OpenNotificationDelivery = Readonly<{
  scheduledFor: Date;
  windowStart: Date;
  windowEnd: Date;
  status: Exclude<NotificationDeliveryStatus, "sent" | "skipped">;
}>;

export type EligibleNotificationUser = Readonly<{
  userId: string;
  cadence: NotificationCadence;
  notificationsStateChangedAt: Date;
  lastProcessedWindowEnd: Date | null;
  openDelivery: OpenNotificationDelivery | null;
  watchlists: readonly EligibleNotificationWatchlist[];
}>;

export type NotificationDeliveryClaim = Readonly<{
  id: string;
  leaseAcquiredAt: Date;
}>;

export type NotificationClaimResult =
  | Readonly<{ kind: "claimed"; claim: NotificationDeliveryClaim }>
  | Readonly<{ kind: "duplicate"; status: NotificationDeliveryStatus }>;

export type NotificationMatchSummary = Readonly<{
  postings: readonly MatchedWatchlistPosting[];
  uniqueMatchCount: number;
  watchlistMatchCount: number;
  truncated: boolean;
}>;

export type NotificationDeliveryPlan = Readonly<{
  deliveryId: string;
  userId: string;
  cadence: NotificationCadence;
  scheduledFor: Date;
  windowStart: Date;
  windowEnd: Date;
  idempotencyKey: string;
  totalMatches: number;
  watchlistMatchCount: number;
  sourceResultsTruncated: boolean;
  displayPostings: readonly MatchedWatchlistPosting[];
}>;

export type NotificationRunTelemetry = Readonly<{
  mode: NotificationExecutionMode;
  eligibleUsers: number;
  due: number;
  claimed: number;
  duplicate: number;
  unknown: number;
  matched: number;
  matchedJobs: number;
  empty: number;
  deferred: number;
  failed: number;
  stateConflicts: number;
  quotaDailyRemaining: number;
  quotaMonthlyRemaining: number;
  durationMs: number;
}>;

export type NotificationSchedulerRepository = Readonly<{
  listEligibleUsers(): Promise<readonly EligibleNotificationUser[]>;
  claim(input: {
    userId: string;
    cadence: NotificationCadence;
    scheduledFor: Date;
    windowStart: Date;
    windowEnd: Date;
    idempotencyKey: string;
    now: Date;
  }): Promise<NotificationClaimResult>;
  markSkipped(input: {
    claim: NotificationDeliveryClaim;
    completedAt: Date;
  }): Promise<boolean>;
  markFailed(input: {
    claim: NotificationDeliveryClaim;
    errorCode: "matcher_error";
    failedAt: Date;
  }): Promise<boolean>;
  markQuotaDeferred(input: {
    claim: NotificationDeliveryClaim;
    matchCount: number;
    deferredUntil: Date;
    deferredAt: Date;
  }): Promise<boolean>;
  recordShadowPlan(input: {
    claim: NotificationDeliveryClaim;
    matchCount: number;
    plannedAt: Date;
  }): Promise<boolean>;
}>;

export type NotificationSchedulerDependencies = Readonly<{
  repository: NotificationSchedulerRepository;
  match(input: {
    watchlists: readonly (EligibleNotificationWatchlist & {
      windowStart: Date;
    })[];
    windowEnd: Date;
  }): Promise<NotificationMatchSummary>;
  now?: () => Date;
}>;

type WorkItem = Readonly<{
  user: EligibleNotificationUser;
  scheduledFor: Date;
  originalWindow: UtcWindow | null;
}>;

type MatchOutcome =
  | Readonly<{ kind: "matched"; work: WorkItem; claim: NotificationDeliveryClaim; match: NotificationMatchSummary; window: UtcWindow; idempotencyKey: string }>
  | Readonly<{ kind: "claimed_empty" }>
  | Readonly<{ kind: "duplicate" }>
  | Readonly<{ kind: "unknown" }>
  | Readonly<{ kind: "failed" }>
  | Readonly<{ kind: "state_conflict" }>
  | Readonly<{ kind: "not_due" }>;

function emptyTelemetry(
  mode: NotificationExecutionMode,
  quota: NotificationQuotaState,
): NotificationRunTelemetry {
  return {
    mode,
    eligibleUsers: 0,
    due: 0,
    claimed: 0,
    duplicate: 0,
    unknown: 0,
    matched: 0,
    matchedJobs: 0,
    empty: 0,
    deferred: 0,
    failed: 0,
    stateConflicts: 0,
    quotaDailyRemaining: Math.max(0, quota.dailyCap - quota.dailyUsed),
    quotaMonthlyRemaining: Math.max(0, quota.monthlyCap - quota.monthlyUsed),
    durationMs: 0,
  };
}

function workItemsForUser(
  user: EligibleNotificationUser,
  sweep: UtcWindow,
): WorkItem[] {
  if (user.openDelivery) {
    return [{
      user,
      scheduledFor: user.openDelivery.scheduledFor,
      originalWindow: {
        windowStart: user.openDelivery.windowStart,
        windowEnd: user.openDelivery.windowEnd,
      },
    }];
  }
  const [scheduledFor] = getNotificationScheduleSlots({
    userId: user.userId,
    cadence: user.cadence,
    sweep,
  });
  return scheduledFor ? [{ user, scheduledFor, originalWindow: null }] : [];
}

function eligibleWindows(work: WorkItem): Array<
  EligibleNotificationWatchlist & { windowStart: Date }
> {
  return work.user.watchlists.flatMap((watchlist) => {
    const windowStart = getNotificationWindowFloor({
      alertsEnabledAt: watchlist.alertsEnabledAt,
      notificationsStateChangedAt: work.user.notificationsStateChangedAt,
      lastProcessedWindowEnd: work.user.lastProcessedWindowEnd,
    });
    return windowStart.getTime() < work.scheduledFor.getTime()
      ? [{ ...watchlist, windowStart }]
      : [];
  });
}

/**
 * Build durable shadow plans only. There is deliberately no provider callback
 * or live mode in this contract, making external delivery impossible here.
 */
export async function runNotificationSchedulerCore(
  input: {
    mode?: NotificationExecutionMode;
    sweep: UtcWindow;
    quota: NotificationQuotaState;
    concurrency: number;
  },
  dependencies: NotificationSchedulerDependencies,
): Promise<{
  plans: readonly NotificationDeliveryPlan[];
  telemetry: NotificationRunTelemetry;
}> {
  const mode = input.mode ?? DEFAULT_NOTIFICATION_EXECUTION_MODE;
  const startedAt = dependencies.now?.() ?? new Date();
  let telemetry = emptyTelemetry(mode, input.quota);
  if (mode === "off") return { plans: [], telemetry };
  assertUtcWindow(input.sweep);

  const users = await dependencies.repository.listEligibleUsers();
  const work = users.flatMap((user) => workItemsForUser(user, input.sweep));
  telemetry = { ...telemetry, eligibleUsers: users.length, due: work.length };

  const outcomes = await mapWithConcurrency(
    work,
    input.concurrency,
    async (item): Promise<MatchOutcome> => {
      if (item.user.openDelivery?.status === "unknown") {
        return { kind: "unknown" };
      }
      const watchlists = eligibleWindows(item);
      if (watchlists.length === 0 && !item.originalWindow) {
        return { kind: "not_due" };
      }
      const windowStart = watchlists.length > 0
        ? new Date(Math.min(...watchlists.map((entry) => entry.windowStart.getTime())))
        : item.originalWindow!.windowStart;
      const windowEnd = item.scheduledFor;
      const idempotencyKey = createNotificationDeliveryIdempotencyKey({
        userId: item.user.userId,
        cadence: item.user.cadence,
        scheduledFor: item.scheduledFor,
      });
      const claimed = await dependencies.repository.claim({
        userId: item.user.userId,
        cadence: item.user.cadence,
        scheduledFor: item.scheduledFor,
        windowStart,
        windowEnd,
        idempotencyKey,
        now: startedAt,
      });
      if (claimed.kind === "duplicate") return { kind: "duplicate" };

      if (watchlists.length === 0) {
        return (await dependencies.repository.markSkipped({
          claim: claimed.claim,
          completedAt: startedAt,
        }))
          ? { kind: "claimed_empty" }
          : { kind: "state_conflict" };
      }

      let match: NotificationMatchSummary;
      try {
        match = await dependencies.match({ watchlists, windowEnd });
      } catch {
        return (await dependencies.repository.markFailed({
          claim: claimed.claim,
          errorCode: "matcher_error",
          failedAt: startedAt,
        }))
          ? { kind: "failed" }
          : { kind: "state_conflict" };
      }

      if (match.uniqueMatchCount === 0) {
        return (await dependencies.repository.markSkipped({
          claim: claimed.claim,
          completedAt: startedAt,
        }))
          ? { kind: "claimed_empty" }
          : { kind: "state_conflict" };
      }
      return {
        kind: "matched",
        work: item,
        claim: claimed.claim,
        match,
        window: { windowStart, windowEnd },
        idempotencyKey,
      };
    },
  );

  const plans: NotificationDeliveryPlan[] = [];
  let quota = input.quota;
  const claimedCount = outcomes.filter((result) =>
    ["matched", "claimed_empty", "failed", "state_conflict"].includes(result.kind),
  ).length;
  const duplicate = outcomes.filter((result) => result.kind === "duplicate").length;
  const unknown = outcomes.filter((result) => result.kind === "unknown").length;
  const empty = outcomes.filter((result) => result.kind === "claimed_empty").length;
  const failed = outcomes.filter((result) => result.kind === "failed").length;
  let stateConflicts = outcomes.filter((result) => result.kind === "state_conflict").length;
  let deferred = 0;
  let matched = 0;
  let matchedJobs = 0;

  for (const outcome of outcomes) {
    if (outcome.kind !== "matched") continue;
    matched += 1;
    matchedJobs += outcome.match.uniqueMatchCount;
    const quotaDecision = calculateNotificationQuota({
      state: quota,
      requested: 1,
      now: startedAt,
    });
    if (!quotaDecision.allowed) {
      const persisted = await dependencies.repository.markQuotaDeferred({
        claim: outcome.claim,
        matchCount: outcome.match.uniqueMatchCount,
        deferredUntil: quotaDecision.deferredUntil!,
        deferredAt: startedAt,
      });
      if (persisted) deferred += 1;
      else stateConflicts += 1;
      continue;
    }
    const persisted = await dependencies.repository.recordShadowPlan({
      claim: outcome.claim,
      matchCount: outcome.match.uniqueMatchCount,
      plannedAt: startedAt,
    });
    if (!persisted) {
      stateConflicts += 1;
      continue;
    }
    quota = quotaDecision.nextState;
    plans.push({
      deliveryId: outcome.claim.id,
      userId: outcome.work.user.userId,
      cadence: outcome.work.user.cadence,
      scheduledFor: outcome.work.scheduledFor,
      windowStart: outcome.window.windowStart,
      windowEnd: outcome.window.windowEnd,
      idempotencyKey: outcome.idempotencyKey,
      totalMatches: outcome.match.uniqueMatchCount,
      watchlistMatchCount: outcome.match.watchlistMatchCount,
      sourceResultsTruncated: outcome.match.truncated,
      displayPostings: outcome.match.postings.slice(
        0,
        NOTIFICATION_DISPLAY_ITEM_LIMIT,
      ),
    });
  }

  const finishedAt = dependencies.now?.() ?? new Date();
  telemetry = {
    ...telemetry,
    claimed: claimedCount,
    duplicate,
    unknown,
    matched,
    matchedJobs,
    empty,
    deferred,
    failed,
    stateConflicts,
    quotaDailyRemaining: Math.max(0, quota.dailyCap - quota.dailyUsed),
    quotaMonthlyRemaining: Math.max(0, quota.monthlyCap - quota.monthlyUsed),
    durationMs: Math.max(0, finishedAt.getTime() - startedAt.getTime()),
  };
  return { plans, telemetry };
}
