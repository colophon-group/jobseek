import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ runCore: vi.fn() }));

vi.mock("@/db", () => ({ db: {} }));
vi.mock("@/lib/services/watchlist-matcher", () => ({
  compileWatchlistMatcherSources: vi.fn(),
  matchCompiledWatchlistsInWindow: vi.fn(),
}));
vi.mock("@/lib/notifications/scheduler-core", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/notifications/scheduler-core")
  >();
  return { ...actual, runNotificationSchedulerCore: mocks.runCore };
});

import { runNotificationScheduler } from "../notification-scheduler";

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
