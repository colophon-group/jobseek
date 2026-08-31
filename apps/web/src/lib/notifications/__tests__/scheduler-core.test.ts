import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  runNotificationSchedulerCore,
  type EligibleNotificationUser,
  type NotificationClaimResult,
  type NotificationSchedulerRepository,
} from "../scheduler-core";
import { getNotificationScheduleSlots } from "../scheduler-policy";
import type { NotificationDeliveryStatus } from "../contracts";
import type { MatchedWatchlistPosting } from "@/lib/watchlist-matcher-contract";

const sweep = {
  windowStart: new Date("2026-08-31T00:00:00.000Z"),
  windowEnd: new Date("2026-09-07T00:00:00.000Z"),
};
const now = new Date("2026-09-07T00:00:00.000Z");
const quota = { dailyCap: 10, monthlyCap: 100, dailyUsed: 0, monthlyUsed: 0 };

function makeUser(overrides: Partial<EligibleNotificationUser> = {}): EligibleNotificationUser {
  return {
    userId: "user-1",
    cadence: "weekly",
    notificationsStateChangedAt: new Date("2026-08-01T00:00:00.000Z"),
    lastProcessedWindowEnd: null,
    openDelivery: null,
    watchlists: [{
      alertsEnabledAt: new Date("2026-08-15T00:00:00.000Z"),
      source: {
        watchlistId: "watchlist-1",
        watchlistLabel: "Backend",
        filters: {},
        companyIds: ["company-1"],
        locale: "en",
        jobLanguages: [],
      },
    }],
    ...overrides,
  };
}

function posting(id: string, labels = [{ id: "watchlist-1", label: "Backend" }]): MatchedWatchlistPosting {
  return {
    id,
    title: `Role ${id}`,
    sourceUrl: `https://example.test/${id}`,
    firstSeenAt: "2026-09-01T12:00:00.000Z",
    isActive: true,
    company: { id: "company-1", name: "Acme", slug: "acme", icon: null },
    matchedWatchlists: labels,
  };
}

function fakeRepository(users: EligibleNotificationUser[]) {
  type Row = { id: string; status: string; matchCount: number | null };
  const rows = new Map<string, Row>();
  const repository: NotificationSchedulerRepository = {
    listEligibleUserCandidatesPage: vi.fn(async ({ afterUserId, limit }) => {
      const ordered = [...users].sort((left, right) =>
        left.userId.localeCompare(right.userId),
      );
      const remaining = afterUserId
        ? ordered.filter((entry) => entry.userId > afterUserId)
        : ordered;
      const selected = remaining.slice(0, limit);
      return {
        candidates: selected.map(({ watchlists: _watchlists, ...candidate }) => candidate),
        nextCursor: remaining.length > limit ? selected.at(-1)!.userId : null,
      };
    }),
    loadEligibleWatchlistSegment: vi.fn(async ({ userId, cursor }) => {
      const owner = users.find((entry) => entry.userId === userId);
      const watchlists = [...(owner?.watchlists ?? [])].sort((left, right) =>
        left.source.watchlistId.localeCompare(right.source.watchlistId),
      );
      const current = cursor?.watchlistId
        ? watchlists.find(
            (entry) => entry.source.watchlistId === cursor.watchlistId,
          )
        : watchlists.find(
            (entry) =>
              !cursor?.afterWatchlistId ||
              entry.source.watchlistId > cursor.afterWatchlistId,
          );
      if (!current) return { watchlist: null, nextCursor: null };
      const companyIds = current.source.companyIds;
      const start = cursor?.afterCompanyId
        ? companyIds.findIndex((id) => id === cursor.afterCompanyId) + 1
        : 0;
      const selected = companyIds.slice(start, start + 100);
      const hasMore = start + selected.length < companyIds.length;
      return {
        watchlist: {
          ...current,
          source: { ...current.source, companyIds: selected },
        },
        nextCursor: hasMore
          ? {
              afterWatchlistId: cursor?.afterWatchlistId ?? null,
              watchlistId: current.source.watchlistId,
              afterCompanyId: selected.at(-1)!,
            }
          : {
              afterWatchlistId: current.source.watchlistId,
              watchlistId: null,
              afterCompanyId: null,
            },
      };
    }),
    claim: vi.fn(async (input): Promise<NotificationClaimResult> => {
      const key = `${input.userId}:${input.cadence}:${input.scheduledFor.toISOString()}`;
      const existing = rows.get(key);
      if (existing) {
        return {
          kind: "duplicate",
          status: existing.status as NotificationDeliveryStatus,
        };
      }
      const row = { id: `delivery-${rows.size + 1}`, status: "pending", matchCount: null };
      rows.set(key, row);
      return { kind: "claimed", claim: { id: row.id, leaseAcquiredAt: input.now } };
    }),
    markSkipped: vi.fn(async ({ claim }) => {
      const row = [...rows.values()].find((value) => value.id === claim.id)!;
      row.status = "skipped";
      row.matchCount = 0;
      return true;
    }),
    markFailed: vi.fn(async ({ claim }) => {
      const row = [...rows.values()].find((value) => value.id === claim.id)!;
      row.status = "failed";
      return true;
    }),
    markQuotaDeferred: vi.fn(async ({ claim, matchCount }) => {
      const row = [...rows.values()].find((value) => value.id === claim.id)!;
      row.status = "quota_deferred";
      row.matchCount = matchCount;
      return true;
    }),
    recordShadowPlan: vi.fn(async ({ claim, matchCount }) => {
      const row = [...rows.values()].find((value) => value.id === claim.id)!;
      row.matchCount = matchCount;
      return true;
    }),
  };
  return { repository, rows };
}

beforeEach(() => vi.clearAllMocks());

describe("providerless notification scheduler core", () => {
  it("is purely off by default and performs no reads or matching", async () => {
    const { repository } = fakeRepository([makeUser()]);
    const match = vi.fn();
    const clock = vi.fn(() => now);
    const result = await runNotificationSchedulerCore(
      { sweep, quota, concurrency: 2 },
      { repository, match, now: clock },
    );
    expect(result.plans).toEqual([]);
    expect(result.telemetry.mode).toBe("off");
    expect(repository.listEligibleUserCandidatesPage).not.toHaveBeenCalled();
    expect(repository.loadEligibleWatchlistSegment).not.toHaveBeenCalled();
    expect(match).not.toHaveBeenCalled();
    expect(clock).not.toHaveBeenCalled();
  });

  it("claims once across duplicate invocation and advances zero-match windows", async () => {
    const { repository, rows } = fakeRepository([makeUser()]);
    const match = vi.fn().mockResolvedValue({
      postings: [], uniqueMatchCount: 0, watchlistMatchCount: 0, truncated: false,
    });
    const input = { mode: "shadow" as const, sweep, quota, concurrency: 2 };
    const first = await runNotificationSchedulerCore(input, { repository, match, now: () => now });
    const second = await runNotificationSchedulerCore(input, { repository, match, now: () => now });
    expect(first.telemetry.empty).toBe(1);
    expect(second.telemetry.duplicate).toBe(1);
    expect(match).toHaveBeenCalledTimes(1);
    expect([...rows.values()][0]).toMatchObject({ status: "skipped", matchCount: 0 });
  });

  it("processes every bounded recovery slot in chronological order", async () => {
    const recoverySweep = {
      windowStart: sweep.windowStart,
      windowEnd: new Date("2026-09-14T00:00:00.000Z"),
    };
    const { repository } = fakeRepository([makeUser()]);
    const match = vi.fn().mockResolvedValue({
      postings: [], uniqueMatchCount: 0, watchlistMatchCount: 0, truncated: false,
    });
    const result = await runNotificationSchedulerCore(
      { mode: "shadow", sweep: recoverySweep, quota, concurrency: 2 },
      { repository, match, now: () => now },
    );
    expect(result.telemetry).toMatchObject({ due: 2, empty: 2 });
    const scheduled = vi.mocked(repository.claim).mock.calls.map(
      ([input]) => input.scheduledFor.getTime(),
    );
    expect(scheduled).toHaveLength(2);
    expect(scheduled).toEqual([...scheduled].sort((left, right) => left - right));
    expect(match).toHaveBeenCalledTimes(2);
  });

  it("continues recovery past a pre-enable slot to the next eligible slot", async () => {
    const recoverySweep = {
      windowStart: sweep.windowStart,
      windowEnd: new Date("2026-09-14T00:00:00.000Z"),
    };
    const slots = getNotificationScheduleSlots({
      userId: "user-1",
      cadence: "weekly",
      sweep: recoverySweep,
    });
    const enabledBetweenSlots = new Date(
      slots[0]!.getTime() + (slots[1]!.getTime() - slots[0]!.getTime()) / 2,
    );
    const user = makeUser({
      watchlists: [{
        ...makeUser().watchlists[0]!,
        alertsEnabledAt: enabledBetweenSlots,
      }],
    });
    const { repository } = fakeRepository([user]);
    const match = vi.fn().mockResolvedValue({
      postings: [], uniqueMatchCount: 0, watchlistMatchCount: 0, truncated: false,
    });
    const result = await runNotificationSchedulerCore(
      { mode: "shadow", sweep: recoverySweep, quota, concurrency: 1 },
      { repository, match, now: () => now },
    );
    expect(result.telemetry).toMatchObject({ due: 2, empty: 1 });
    expect(repository.claim).toHaveBeenCalledTimes(1);
    expect(vi.mocked(repository.claim).mock.calls[0]![0].scheduledFor).toEqual(
      slots[1],
    );
  });

  it("uses independent enable/resume floors, retains labels, and caps display at 20", async () => {
    const resumedAt = new Date("2026-08-25T10:00:00.000Z");
    const user = makeUser({
      notificationsStateChangedAt: resumedAt,
      lastProcessedWindowEnd: new Date("2026-08-20T00:00:00.000Z"),
      watchlists: [
        makeUser().watchlists[0]!,
        {
          alertsEnabledAt: new Date("2026-08-28T09:00:00.000Z"),
          source: { ...makeUser().watchlists[0]!.source, watchlistId: "watchlist-2", watchlistLabel: "Remote" },
        },
      ],
    });
    const { repository } = fakeRepository([user]);
    const postings = Array.from({ length: 25 }, (_, index) =>
      posting(`job-${index}`, index === 0
        ? [{ id: "watchlist-1", label: "Backend" }, { id: "watchlist-2", label: "Remote" }]
        : undefined),
    );
    const match = vi.fn().mockImplementation(async ({ watchlists }) =>
      watchlists[0]!.source.watchlistId === "watchlist-1"
        ? {
            postings: postings.map((entry) => ({
              ...entry,
              matchedWatchlists: [{ id: "watchlist-1", label: "Backend" }],
            })),
            uniqueMatchCount: 25,
            watchlistMatchCount: 25,
            truncated: false,
          }
        : {
            postings: [posting("job-0", [{ id: "watchlist-2", label: "Remote" }])],
            uniqueMatchCount: 1,
            watchlistMatchCount: 1,
            truncated: false,
          },
    );
    const result = await runNotificationSchedulerCore(
      { mode: "shadow", sweep, quota, concurrency: 2 },
      { repository, match, now: () => now },
    );
    expect(match.mock.calls.map((call) => call[0].watchlists[0]!.windowStart)).toEqual([
      resumedAt,
      new Date("2026-08-28T09:00:00.000Z"),
    ]);
    expect(result.plans[0]).toMatchObject({ totalMatches: 25, watchlistMatchCount: 26 });
    expect(result.plans[0]!.displayPostings).toHaveLength(20);
    expect(result.plans[0]!.displayPostings[0]!.matchedWatchlists).toHaveLength(2);
  });

  it("defers a matched plan using quota policy without emitting a provider effect", async () => {
    const { repository, rows } = fakeRepository([makeUser()]);
    const match = vi.fn().mockResolvedValue({
      postings: [posting("job-1")], uniqueMatchCount: 1, watchlistMatchCount: 1, truncated: false,
    });
    const result = await runNotificationSchedulerCore(
      {
        mode: "shadow",
        sweep,
        quota: { dailyCap: 0, monthlyCap: 0, dailyUsed: 0, monthlyUsed: 0 },
        concurrency: 1,
      },
      { repository, match, now: () => now },
    );
    expect(result.plans).toEqual([]);
    expect(result.telemetry.deferred).toBe(1);
    expect([...rows.values()][0]).toMatchObject({ status: "quota_deferred", matchCount: 1 });
  });

  it("fails matcher work safely and never retries an unknown provider outcome", async () => {
    const scheduledFor = getNotificationScheduleSlots({
      userId: "user-1", cadence: "weekly", sweep,
    })[0]!;
    const unknownUser = makeUser({
      openDelivery: {
        scheduledFor,
        windowStart: new Date("2026-08-15T00:00:00.000Z"),
        windowEnd: scheduledFor,
        status: "unknown",
      },
    });
    const unknownRepo = fakeRepository([unknownUser]).repository;
    const unknownMatch = vi.fn();
    const unknown = await runNotificationSchedulerCore(
      { mode: "shadow", sweep, quota, concurrency: 1 },
      { repository: unknownRepo, match: unknownMatch, now: () => now },
    );
    expect(unknown.telemetry.unknown).toBe(1);
    expect(unknownRepo.claim).not.toHaveBeenCalled();
    expect(unknownMatch).not.toHaveBeenCalled();

    const failedRepo = fakeRepository([makeUser()]).repository;
    const failed = await runNotificationSchedulerCore(
      { mode: "shadow", sweep, quota, concurrency: 1 },
      { repository: failedRepo, match: vi.fn().mockRejectedValue(new Error("private details")), now: () => now },
    );
    expect(failed.telemetry.failed).toBe(1);
    expect(failedRepo.markFailed).toHaveBeenCalledWith(expect.objectContaining({ errorCode: "matcher_error" }));
  });

  it("does no work when verified/unpaused eligibility selection is empty", async () => {
    const { repository } = fakeRepository([]);
    const match = vi.fn();
    const result = await runNotificationSchedulerCore(
      { mode: "shadow", sweep, quota, concurrency: 1 },
      { repository, match, now: () => now },
    );
    expect(result.telemetry).toMatchObject({ eligibleUsers: 0, due: 0 });
    expect(match).not.toHaveBeenCalled();
  });

  it("pages large owner cohorts without starving owners after page 50", async () => {
    const users = Array.from({ length: 60 }, (_, index) =>
      makeUser({ userId: `user-${String(index).padStart(3, "0")}` }),
    );
    const { repository } = fakeRepository(users);
    const match = vi.fn().mockResolvedValue({
      postings: [], uniqueMatchCount: 0, watchlistMatchCount: 0, truncated: false,
    });
    const first = await runNotificationSchedulerCore(
      { mode: "shadow", sweep, quota, concurrency: 4 },
      { repository, match, now: () => now },
    );
    expect(first.telemetry.eligibleUsers).toBe(50);
    expect(first.continuation).toEqual({ afterUserId: "user-049" });
    const firstPageHydratedUsers = new Set(
      vi.mocked(repository.loadEligibleWatchlistSegment).mock.calls.map(
        ([input]) => input.userId,
      ),
    );
    expect(firstPageHydratedUsers).toHaveLength(50);

    const second = await runNotificationSchedulerCore(
      {
        mode: "shadow",
        sweep,
        quota,
        concurrency: 4,
        cursor: first.continuation!.afterUserId,
      },
      { repository, match, now: () => now },
    );
    expect(second.telemetry.eligibleUsers).toBe(10);
    expect(second.continuation).toBeNull();
    const allHydratedUsers = new Set(
      vi.mocked(repository.loadEligibleWatchlistSegment).mock.calls.map(
        ([input]) => input.userId,
      ),
    );
    expect(allHydratedUsers).toHaveLength(60);
    expect(allHydratedUsers.has("user-059")).toBe(true);
    for (const [input] of vi.mocked(repository.listEligibleUserCandidatesPage).mock.calls) {
      expect(input.limit).toBe(50);
    }
  });

  it("streams grandfathered watchlists and high-cardinality memberships in hard segments", async () => {
    const watchlists = Array.from({ length: 12 }, (_, watchlistIndex) => ({
      alertsEnabledAt: new Date("2026-08-15T00:00:00.000Z"),
      source: {
        ...makeUser().watchlists[0]!.source,
        watchlistId: `watchlist-${String(watchlistIndex).padStart(2, "0")}`,
        watchlistLabel: `Watchlist ${watchlistIndex}`,
        companyIds: Array.from(
          { length: 225 },
          (_, companyIndex) =>
            `company-${String(watchlistIndex).padStart(2, "0")}-${String(companyIndex).padStart(3, "0")}`,
        ),
      },
    }));
    const { repository } = fakeRepository([makeUser({ watchlists })]);
    const match = vi.fn().mockResolvedValue({
      postings: [], uniqueMatchCount: 0, watchlistMatchCount: 0, truncated: false,
    });
    const result = await runNotificationSchedulerCore(
      { mode: "shadow", sweep, quota, concurrency: 1 },
      { repository, match, now: () => now },
    );
    expect(result.telemetry.empty).toBe(1);
    const matchedSegments = match.mock.calls.map(
      (call) => call[0].watchlists[0]!,
    );
    expect(new Set(
      matchedSegments.map((entry) => entry.source.watchlistId),
    )).toHaveLength(12);
    expect(Math.max(
      ...matchedSegments.map((entry) => entry.source.companyIds.length),
    )).toBe(100);
    expect(matchedSegments).toHaveLength(36);
  });
});
