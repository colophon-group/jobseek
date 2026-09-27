import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ runCore: vi.fn(), compile: vi.fn(), match: vi.fn(), narrow: vi.fn() }));

vi.mock("@/db", () => ({ db: {} }));
vi.mock("@/lib/services/watchlist-matcher", () => ({
  compileWatchlistMatcherSources: mocks.compile,
  matchCompiledWatchlistsInWindow: mocks.match,
}));
vi.mock("@/lib/notifications/scheduler-core", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/notifications/scheduler-core")
  >();
  return { ...actual, runNotificationSchedulerCore: mocks.runCore };
});

vi.mock("../notification-narrowing", () => ({ matchNarrowedNotificationWatchlist: mocks.narrow }));

import { buildWatchlistCandidateWindowFilter } from "@/lib/search/watchlist-candidate-query";
import { matchNotificationWatchlists, runNotificationScheduler } from "../notification-scheduler";

describe("notification scheduler service wrapper", () => {
  it("forwards owner continuations so later pages cannot starve", async () => {
    mocks.runCore
      .mockResolvedValueOnce({
        plans: [],
        telemetry: {},
        continuation: { afterUserId: "user-049" },
      })
      .mockResolvedValueOnce({
        plans: [],
        telemetry: {},
        continuation: null,
      });
    const input = {
      mode: "shadow" as const,
      sweep: {
        windowStart: new Date("2026-08-31T00:00:00.000Z"),
        windowEnd: new Date("2026-09-07T00:00:00.000Z"),
      },
      quota: {
        dailyCap: 10,
        monthlyCap: 100,
        dailyUsed: 0,
        monthlyUsed: 0,
      },
      concurrency: 2,
    };

    const first = await runNotificationScheduler(input);
    await runNotificationScheduler({
      ...input,
      cursor: first.continuation!.afterUserId,
    });

    expect(mocks.runCore).toHaveBeenNthCalledWith(
      1,
      input,
      expect.any(Object),
    );
    expect(mocks.runCore).toHaveBeenNthCalledWith(
      2,
      { ...input, cursor: "user-049" },
      expect.any(Object),
    );
  });
});

const source = { watchlistId: "list", watchlistLabel: "Roles", filters: { anyCompany: true }, companyIds: [], locale: "en", jobLanguages: [] };
const empty = { postings: [], watchlists: [{ total: 0, truncated: false }] };
it("keeps millisecond opt-in boundaries exclusive of older indexed jobs", async () => {
  const start = new Date("2026-09-20T10:00:00.123Z");
  const end = new Date("2026-09-27T10:00:00.456Z");
  mocks.compile.mockResolvedValue([{ ...source, candidateFilters: { anyCompany: true, companyIds: [] } }]);
  const matcher = mocks.match;
  matcher.mockImplementation(async (window) => {
    // Exercise the real strict compiler that rejected production's opt-in timestamps.
    expect(buildWatchlistCandidateWindowFilter(window)).toBe(`first_seen_at:>=${Date.parse("2026-09-20T10:00:01Z") / 1000} && first_seen_at:<${Date.parse("2026-09-27T10:00:01Z") / 1000}`);
    expect(window.windowStart.toISOString()).toBe("2026-09-20T10:00:01.000Z");
    expect(window.windowEnd.toISOString()).toBe("2026-09-27T10:00:01.000Z");
    for (const offset of [-1, 0, 1, 604800, 604801]) {
      const indexedAt = Math.floor(start.getTime() / 1000) * 1000 + offset * 1000;
      expect(indexedAt >= +window.windowStart && indexedAt < +window.windowEnd).toBe(indexedAt >= +start && indexedAt < +end);
    }
    return empty;
  });
  await matchNotificationWatchlists({ windowEnd: end, watchlists: [{ ownerId: "owner", narrowedOnly: false, alertsEnabledAt: start, windowStart: start, source }] });
  expect(matcher).toHaveBeenCalled();
});
it("skips intervals containing no representable indexed timestamp", async () => {
  mocks.compile.mockClear(); mocks.match.mockClear(); mocks.narrow.mockClear();
  mocks.compile.mockResolvedValue([]);
  const start = new Date("2026-09-27T10:00:00.123Z");
  const result = await matchNotificationWatchlists({ windowEnd: new Date("2026-09-27T10:00:00.456Z"), watchlists: [{ alertsEnabledAt: start, windowStart: start, source }] });
  expect(result.uniqueMatchCount).toBe(0);
  expect(mocks.match).not.toHaveBeenCalled();
  expect(mocks.narrow).not.toHaveBeenCalled();
});

it("keeps exact decision-completion timestamps when delegating narrowed matching", async () => {
  const start = new Date("2026-09-20T10:00:00.123Z");
  const end = new Date("2026-09-27T10:00:00.456Z");
  mocks.compile.mockResolvedValue([{ ...source, candidateFilters: { anyCompany: true, companyIds: [] } }]);
  mocks.narrow.mockResolvedValue(empty);
  await matchNotificationWatchlists({ windowEnd: end, watchlists: [{ ownerId: "owner", narrowedOnly: true, alertsEnabledAt: start, windowStart: start, source }] });
  expect(mocks.narrow).toHaveBeenLastCalledWith(expect.objectContaining({windowStart:start, windowEnd:end}));
});
