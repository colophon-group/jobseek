import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({
  config: { mode: "off", dailyCap: 75, monthlyCap: 2400, internalUserIds: [] as string[] },
  get: vi.fn(), set: vi.fn(), eval: vi.fn(), schedule: vi.fn(), deliver: vi.fn(), quota: vi.fn(),
}));
vi.mock("@/lib/redis", () => ({ redis: { get: mocks.get, set: mocks.set, eval: mocks.eval } }));
vi.mock("@/lib/notifications/config", () => ({ getJobAlertsConfig: () => mocks.config }));
vi.mock("../notification-scheduler", () => ({ runNotificationScheduler: mocks.schedule }));
vi.mock("../notification-delivery", () => ({ deliverNotificationPlan: mocks.deliver, readNotificationQuota: mocks.quota }));
vi.mock("../notification-retention", () => ({ cleanupNotificationRetention: async () => {} }));
vi.mock("node:timers/promises", () => ({ setTimeout: async () => {}, default: { setTimeout: async () => {} } }));
import { runJobAlerts } from "../notification-runner";
const telemetry = { due: 1, matched: 1, empty: 0, failed: 0, unknown: 0, deferred: 0, duplicate: 0, quotaDailyRemaining: 74, quotaMonthlyRemaining: 2399 };
beforeEach(() => {
  vi.clearAllMocks(); mocks.config.mode = "live"; mocks.config.internalUserIds = [];
  const values = new Map<string, unknown>();
  mocks.set.mockImplementation(async (key, value) => { values.set(key, value); return "OK"; });
  mocks.get.mockImplementation(async key => values.get(key) ?? null);
  mocks.eval.mockResolvedValue(1);
  mocks.quota.mockResolvedValue({ dailyCap: 75, monthlyCap: 2400, dailyUsed: 0, monthlyUsed: 0 });
  mocks.deliver.mockResolvedValue("sent");
  mocks.schedule.mockResolvedValue({ plans: [{ deliveryId: "fixture" }], telemetry, continuation: null });
  vi.spyOn(console, "info").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());
describe("notification runner", () => {
  it("does no database, Redis, matching or provider work while off", async () => {
    mocks.config.mode = "off";
    expect(await runJobAlerts()).toEqual({ mode: "off", status: "off" });
    expect(mocks.set).not.toHaveBeenCalled(); expect(mocks.quota).not.toHaveBeenCalled();
    expect(mocks.schedule).not.toHaveBeenCalled(); expect(mocks.deliver).not.toHaveBeenCalled();
  });
  it("rejects overlapping sweeps before matching", async () => {
    mocks.set.mockResolvedValueOnce(null);
    expect((await runJobAlerts()).status).toBe("overlap");
    expect(mocks.schedule).not.toHaveBeenCalled();
  });
  it("plans in shadow without invoking delivery and carries quota across pages", async () => {
    mocks.config.mode = "shadow";
    mocks.schedule.mockResolvedValueOnce({ plans: [], telemetry, continuation: { afterUserId: "owner-10" } });
    const result = await runJobAlerts();
    expect(result.status).toBe("complete"); expect(mocks.deliver).not.toHaveBeenCalled();
    expect(mocks.schedule.mock.calls[1]![0]).toMatchObject({ cursor: "owner-10", quota: { dailyUsed: 1, monthlyUsed: 1 } });
  });
  it("isolates uncertain delivery failures and resumes owner pages", async () => {
    mocks.schedule.mockResolvedValueOnce({ plans: [{ deliveryId: "a" }, { deliveryId: "b" }], telemetry, continuation: { afterUserId: "owner-10" } });
    mocks.deliver.mockRejectedValueOnce(new Error("lost commit"));
    const result = await runJobAlerts();
    expect(result).toMatchObject({ status: "complete", sent: 2, unknown: 1 });
    expect(mocks.schedule.mock.calls[1]![0].cursor).toBe("owner-10");
    expect(mocks.eval).toHaveBeenCalledOnce();
  });
  it("resumes unfinished owners across UTC day boundaries", async () => {
    const originalGet = mocks.get.getMockImplementation()!;
    mocks.get.mockImplementation(async key => key.includes(":state:") ? {
      mode: "live", day: "2026-01-01", sweepEnd: "2026-01-01T03:00:00Z", cursor: "late-owner", complete: false,
    } : originalGet(key));
    await runJobAlerts();
    expect(mocks.schedule.mock.calls[0]![0].cursor).toBe("late-owner");
    expect(mocks.schedule.mock.calls[0]![0].sweep.windowEnd.toISOString()).toBe("2026-01-01T03:00:00.000Z");
  });
  it("filters internal recipients at eligibility and avoids reprocessing a completed daily sweep", async () => {
    mocks.config.mode = "internal"; mocks.config.internalUserIds = ["test-owner"];
    await runJobAlerts();
    expect(mocks.schedule.mock.calls[0]![0].internalUserIds).toEqual(["test-owner"]);
    await runJobAlerts();
    expect(mocks.schedule).toHaveBeenCalledOnce();
  });
});
