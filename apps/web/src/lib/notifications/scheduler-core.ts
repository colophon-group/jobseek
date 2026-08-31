import "server-only";

import type {
  NotificationCadence,
  NotificationDeliveryStatus,
} from "./contracts";
import {
  advancesNotificationWindow,
  createNotificationDeliveryIdempotencyKey,
  getNotificationWindowFloor,
} from "./policy";
import {
  DEFAULT_NOTIFICATION_EXECUTION_MODE,
  NOTIFICATION_DISPLAY_ITEM_LIMIT,
  NOTIFICATION_ELIGIBLE_OWNER_PAGE_SIZE,
  MAX_NOTIFICATION_HYDRATION_CURSOR_PROBES_PER_PERIOD,
  MAX_NOTIFICATION_HYDRATION_SEGMENTS_PER_PERIOD,
  NOTIFICATION_MATCH_AGGREGATE_ITEM_LIMIT,
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
import { canonicalStringCompare } from "@/lib/sort";

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

export type EligibleNotificationUserCandidate = Readonly<{
  userId: string;
  cadence: NotificationCadence;
  notificationsStateChangedAt: Date;
  lastProcessedWindowEnd: Date | null;
  openDelivery: OpenNotificationDelivery | null;
}>;

export type EligibleNotificationUser = EligibleNotificationUserCandidate &
  Readonly<{ watchlists: readonly EligibleNotificationWatchlist[] }>;

export type NotificationWatchlistHydrationCursor = Readonly<{
  afterWatchlistId: string | null;
  watchlistId: string | null;
  afterCompanyId: string | null;
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
  listEligibleUserCandidatesPage(input: {
    afterUserId: string | null;
    limit: number;
  }): Promise<Readonly<{
    candidates: readonly EligibleNotificationUserCandidate[];
    nextCursor: string | null;
  }>>;
  loadEligibleWatchlistSegment(input: {
    userId: string;
    cursor: NotificationWatchlistHydrationCursor | null;
  }): Promise<Readonly<{
    watchlist: EligibleNotificationWatchlist | null;
    nextCursor: NotificationWatchlistHydrationCursor | null;
  }>>;
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
  user: EligibleNotificationUserCandidate;
  scheduledFor: Date;
  originalWindow: UtcWindow | null;
}>;

type MatchOutcome =
  | Readonly<{ kind: "matched"; work: WorkItem; claim: NotificationDeliveryClaim; match: NotificationMatchSummary; window: UtcWindow; idempotencyKey: string }>
  | Readonly<{ kind: "claimed_empty"; scheduledFor: Date }>
  | Readonly<{ kind: "duplicate"; status: NotificationDeliveryStatus; scheduledFor: Date }>
  | Readonly<{ kind: "unknown" }>
  | Readonly<{ kind: "failed" }>
  | Readonly<{ kind: "hydration_failed" }>
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
  user: EligibleNotificationUserCandidate,
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
  return getNotificationScheduleSlots({
    userId: user.userId,
    cadence: user.cadence,
    sweep,
  }).map((scheduledFor) => ({ user, scheduledFor, originalWindow: null }));
}

function candidateIsDue(
  candidate: EligibleNotificationUserCandidate,
  sweep: UtcWindow,
): boolean {
  return candidate.openDelivery !== null || getNotificationScheduleSlots({
    userId: candidate.userId,
    cadence: candidate.cadence,
    sweep,
  }).length > 0;
}

function eligibleWindows(
  work: WorkItem,
  watchlists: readonly EligibleNotificationWatchlist[],
  lastProcessedWindowEnd: Date | null,
): Array<EligibleNotificationWatchlist & { windowStart: Date }> {
  return watchlists.flatMap((watchlist) => {
    const windowStart = getNotificationWindowFloor({
      alertsEnabledAt: watchlist.alertsEnabledAt,
      notificationsStateChangedAt: work.user.notificationsStateChangedAt,
      lastProcessedWindowEnd,
    });
    return windowStart.getTime() < work.scheduledFor.getTime()
      ? [{ ...watchlist, windowStart }]
      : [];
  });
}

/**
 * Build durable shadow plans only. There is deliberately no provider callback
 * or live mode in this contract, making external delivery impossible here.
 * One keyset owner page is processed per call; `continuation` reaches the next.
 */
export async function runNotificationSchedulerCore(
  input: {
    mode?: NotificationExecutionMode;
    sweep: UtcWindow;
    quota: NotificationQuotaState;
    concurrency: number;
    cursor?: string | null;
  },
  dependencies: NotificationSchedulerDependencies,
): Promise<{
  plans: readonly NotificationDeliveryPlan[];
  telemetry: NotificationRunTelemetry;
  continuation: Readonly<{ afterUserId: string }> | null;
}> {
  const mode = input.mode ?? DEFAULT_NOTIFICATION_EXECUTION_MODE;
  if (mode === "off") {
    return {
      plans: [],
      telemetry: emptyTelemetry(mode, input.quota),
      continuation: null,
    };
  }

  assertUtcWindow(input.sweep);
  const startedAt = dependencies.now?.() ?? new Date();
  let telemetry = emptyTelemetry(mode, input.quota);
  const page = await dependencies.repository.listEligibleUserCandidatesPage({
    afterUserId: input.cursor ?? null,
    limit: NOTIFICATION_ELIGIBLE_OWNER_PAGE_SIZE,
  });
  const dueCandidates = page.candidates.filter((candidate) =>
    candidateIsDue(candidate, input.sweep),
  );
  const workByUser = dueCandidates.map((user) =>
    workItemsForUser(user, input.sweep),
  );
  telemetry = {
    ...telemetry,
    eligibleUsers: page.candidates.length,
    due: workByUser.reduce((total, work) => total + work.length, 0),
  };

  async function visitWatchlistSegments(
    userId: string,
    visitor: (watchlist: EligibleNotificationWatchlist) => Promise<void> | void,
  ): Promise<void> {
    let cursor: NotificationWatchlistHydrationCursor | null = null;
    let visitedSegments = 0;
    for (
      let probe = 0;
      probe < MAX_NOTIFICATION_HYDRATION_CURSOR_PROBES_PER_PERIOD;
      probe += 1
    ) {
      const page = await dependencies.repository.loadEligibleWatchlistSegment({
        userId,
        cursor,
      });
      if (page.watchlist) {
        if (visitedSegments >= MAX_NOTIFICATION_HYDRATION_SEGMENTS_PER_PERIOD) {
          throw new Error("Notification hydration segment budget exhausted");
        }
        visitedSegments += 1;
        await visitor(page.watchlist);
      }
      if (!page.nextCursor) return;
      if (JSON.stringify(page.nextCursor) === JSON.stringify(cursor)) {
        throw new Error("Notification hydration cursor did not advance");
      }
      cursor = page.nextCursor;
    }
    throw new Error("Notification hydration cursor probe budget exhausted");
  }

  async function processWork(
    item: WorkItem,
    lastProcessedWindowEnd: Date | null,
  ): Promise<MatchOutcome> {
    if (item.user.openDelivery?.status === "unknown") {
      return { kind: "unknown" };
    }
    let earliestWindowStart: Date | null = null;
    try {
      await visitWatchlistSegments(item.user.userId, (watchlist) => {
        const [eligible] = eligibleWindows(
          item,
          [watchlist],
          lastProcessedWindowEnd,
        );
        if (
          eligible &&
          (!earliestWindowStart ||
            eligible.windowStart.getTime() < earliestWindowStart.getTime())
        ) {
          earliestWindowStart = eligible.windowStart;
        }
      });
    } catch {
      return { kind: "hydration_failed" };
    }
    if (!earliestWindowStart && !item.originalWindow) {
      return { kind: "not_due" };
    }
    const windowStart = earliestWindowStart ?? item.originalWindow!.windowStart;
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
    if (claimed.kind === "duplicate") {
      return {
        kind: "duplicate",
        status: claimed.status,
        scheduledFor: item.scheduledFor,
      };
    }
    if (!earliestWindowStart) {
      return (await dependencies.repository.markSkipped({
        claim: claimed.claim,
        completedAt: startedAt,
      }))
        ? { kind: "claimed_empty", scheduledFor: item.scheduledFor }
        : { kind: "state_conflict" };
    }

    const postings = new Map<string, MatchedWatchlistPosting>();
    let watchlistMatchCount = 0;
    let truncated = false;
    try {
      await visitWatchlistSegments(item.user.userId, async (watchlist) => {
        const watchlists = eligibleWindows(
          item,
          [watchlist],
          lastProcessedWindowEnd,
        );
        if (watchlists.length === 0) return;
        const segmentMatch = await dependencies.match({ watchlists, windowEnd });
        watchlistMatchCount += segmentMatch.watchlistMatchCount;
        truncated ||= segmentMatch.truncated;
        for (const posting of segmentMatch.postings) {
          const existing = postings.get(posting.id);
          if (existing) {
            const labels = new Set(
              existing.matchedWatchlists.map((label) => label.id),
            );
            existing.matchedWatchlists.push(
              ...posting.matchedWatchlists.filter(
                (label) => !labels.has(label.id),
              ),
            );
          } else if (
            postings.size < NOTIFICATION_MATCH_AGGREGATE_ITEM_LIMIT
          ) {
            postings.set(posting.id, {
              ...posting,
              matchedWatchlists: [...posting.matchedWatchlists],
            });
          } else {
            truncated = true;
          }
        }
      });
    } catch {
      return (await dependencies.repository.markFailed({
        claim: claimed.claim,
        errorCode: "matcher_error",
        failedAt: startedAt,
      }))
        ? { kind: "failed" }
        : { kind: "state_conflict" };
    }
    const orderedPostings = [...postings.values()].sort((left, right) =>
      Date.parse(right.firstSeenAt) - Date.parse(left.firstSeenAt) ||
      canonicalStringCompare(left.id, right.id),
    );
    const match: NotificationMatchSummary = {
      postings: orderedPostings,
      uniqueMatchCount: orderedPostings.length,
      watchlistMatchCount,
      truncated,
    };
    if (match.uniqueMatchCount === 0) {
      return (await dependencies.repository.markSkipped({
        claim: claimed.claim,
        completedAt: startedAt,
      }))
        ? { kind: "claimed_empty", scheduledFor: item.scheduledFor }
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
  }

  const outcomeGroups = await mapWithConcurrency(
    workByUser,
    input.concurrency,
    async (work): Promise<MatchOutcome[]> => {
      const outcomes: MatchOutcome[] = [];
      let lastProcessedWindowEnd = work[0]?.user.lastProcessedWindowEnd ?? null;
      for (const item of work) {
        const outcome = await processWork(item, lastProcessedWindowEnd);
        outcomes.push(outcome);
        if (outcome.kind === "claimed_empty") {
          lastProcessedWindowEnd = outcome.scheduledFor;
          continue;
        }
        if (
          outcome.kind === "duplicate" &&
          advancesNotificationWindow(outcome.status)
        ) {
          lastProcessedWindowEnd = outcome.scheduledFor;
          continue;
        }
        if (outcome.kind === "not_due") continue;
        break;
      }
      return outcomes;
    },
  );
  const outcomes = outcomeGroups.flat();

  const plans: NotificationDeliveryPlan[] = [];
  let quota = input.quota;
  const claimedCount = outcomes.filter((result) =>
    ["matched", "claimed_empty", "failed", "state_conflict"].includes(result.kind),
  ).length;
  const duplicate = outcomes.filter((result) => result.kind === "duplicate").length;
  const unknown = outcomes.filter((result) => result.kind === "unknown").length;
  const empty = outcomes.filter((result) => result.kind === "claimed_empty").length;
  const failed = outcomes.filter((result) =>
    result.kind === "failed" || result.kind === "hydration_failed",
  ).length;
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
  return {
    plans,
    telemetry,
    continuation: page.nextCursor
      ? { afterUserId: page.nextCursor }
      : null,
  };
}
