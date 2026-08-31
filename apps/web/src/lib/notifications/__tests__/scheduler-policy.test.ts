import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  MAX_NOTIFICATION_RECOVERY_SLOTS_PER_USER,
  calculateNotificationQuota,
  getNotificationScheduleAssignment,
  getNotificationScheduleSlots,
  mapWithConcurrency,
} from "../scheduler-policy";

describe("notification scheduler policy", () => {
  it("assigns a stable UTC weekday and hour while preserving half-open sweeps", () => {
    const assignment = getNotificationScheduleAssignment({
      userId: "user-1",
      cadence: "weekly",
    });
    expect(assignment).toEqual(
      getNotificationScheduleAssignment({ userId: "user-1", cadence: "weekly" }),
    );
    expect(assignment.weekdayUtc).toBeGreaterThanOrEqual(0);
    expect(assignment.weekdayUtc).toBeLessThan(7);
    expect(assignment.minuteOfDayUtc % 60).toBe(0);

    const broad = getNotificationScheduleSlots({
      userId: "user-1",
      cadence: "weekly",
      sweep: {
        windowStart: new Date("2026-08-31T02:00:00+02:00"),
        windowEnd: new Date("2026-09-07T02:00:00+02:00"),
      },
    });
    expect(broad).toHaveLength(1);
    const slot = broad[0]!;
    expect(getNotificationScheduleSlots({
      userId: "user-1",
      cadence: "weekly",
      sweep: { windowStart: slot, windowEnd: new Date(slot.getTime() + 1) },
    })).toEqual([slot]);
    expect(getNotificationScheduleSlots({
      userId: "user-1",
      cadence: "weekly",
      sweep: { windowStart: new Date(slot.getTime() - 1), windowEnd: slot },
    })).toEqual([]);
  });

  it("rejects recovery sweeps beyond the explicit per-user work bound", () => {
    expect(() => getNotificationScheduleSlots({
      userId: "user-1",
      cadence: "weekly",
      sweep: {
        windowStart: new Date("2026-01-01T00:00:00.000Z"),
        windowEnd: new Date(
          Date.parse("2026-01-01T00:00:00.000Z") +
          (MAX_NOTIFICATION_RECOVERY_SLOTS_PER_USER + 2) * 7 * 24 * 60 * 60 * 1_000,
        ),
      },
    })).toThrow("recovery slots");
  });

  it("calculates daily and monthly deferral without owning cap configuration", () => {
    const now = new Date("2026-08-31T22:30:00.000Z");
    const daily = calculateNotificationQuota({
      state: { dailyCap: 5, monthlyCap: 20, dailyUsed: 5, monthlyUsed: 10 },
      requested: 1,
      now,
    });
    expect(daily.allowed).toBe(false);
    expect(daily.deferredUntil).toEqual(new Date("2026-09-01T00:00:00.000Z"));

    const monthly = calculateNotificationQuota({
      state: { dailyCap: 5, monthlyCap: 20, dailyUsed: 1, monthlyUsed: 20 },
      requested: 1,
      now,
    });
    expect(monthly.allowed).toBe(false);
    expect(monthly.deferredUntil).toEqual(new Date("2026-09-01T00:00:00.000Z"));

    const allowed = calculateNotificationQuota({
      state: { dailyCap: 5, monthlyCap: 20, dailyUsed: 1, monthlyUsed: 10 },
      requested: 1,
      now,
    });
    expect(allowed).toMatchObject({
      allowed: true,
      dailyRemaining: 3,
      monthlyRemaining: 9,
    });
  });

  it("never exceeds the requested in-process concurrency and preserves order", async () => {
    let active = 0;
    let maximum = 0;
    const release: Array<() => void> = [];
    const result = mapWithConcurrency([1, 2, 3, 4], 2, async (value) => {
      active += 1;
      maximum = Math.max(maximum, active);
      await new Promise<void>((resolve) => release.push(resolve));
      active -= 1;
      return value * 2;
    });
    await vi.waitFor(() => expect(release).toHaveLength(2));
    release.shift()!();
    await vi.waitFor(() => expect(release).toHaveLength(2));
    release.shift()!();
    await vi.waitFor(() => expect(release).toHaveLength(2));
    release.shift()!();
    release.shift()!();
    await expect(result).resolves.toEqual([2, 4, 6, 8]);
    expect(maximum).toBe(2);
  });
});
