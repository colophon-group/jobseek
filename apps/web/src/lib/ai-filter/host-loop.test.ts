// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { runHostWorker, WorkerWakeup } from "./host-loop";

afterEach(() => vi.useRealTimers());
const idle = { status: "completed" as const, claimed: 0, started: 0, deferred: 0, failed: 0 };

describe("host worker scheduling", () => {
  it("wakes an idle worker immediately and coalesces notifications arriving during a slice", async () => {
    vi.useFakeTimers();
    const stop = new AbortController();
    const wakeup = new WorkerWakeup();
    const sweep = vi.fn().mockResolvedValue(idle);
    const running = runHostWorker({ signal: stop.signal, wakeup, sweep, report: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    expect(sweep).toHaveBeenCalledTimes(1);
    wakeup.wake();
    await vi.advanceTimersByTimeAsync(0);
    expect(sweep).toHaveBeenCalledTimes(2);
    wakeup.wake();
    wakeup.wake();
    await vi.advanceTimersByTimeAsync(0);
    expect(sweep).toHaveBeenCalledTimes(3);
    stop.abort();
    await running;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("recovers missed notifications and unattended repairs without web requests", async () => {
    vi.useFakeTimers();
    const stop = new AbortController();
    const sweep = vi.fn().mockResolvedValue(idle);
    const running = runHostWorker({ signal: stop.signal, wakeup: new WorkerWakeup(), sweep, report: vi.fn() });
    await vi.advanceTimersByTimeAsync(29_999);
    expect(sweep).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(sweep).toHaveBeenCalledTimes(2);
    stop.abort();
    await running;
  });

  it("continues active slices promptly and preserves backoff despite wake storms", async () => {
    vi.useFakeTimers();
    const stop = new AbortController();
    const wakeup = new WorkerWakeup();
    const sweep = vi.fn().mockResolvedValueOnce({ ...idle, claimed: 1, started: 1 })
      .mockRejectedValueOnce(new Error("private provider details"))
      .mockResolvedValue(idle);
    const report = vi.fn();
    const running = runHostWorker({ signal: stop.signal, wakeup, sweep, report });
    await vi.advanceTimersByTimeAsync(2_000);
    expect(sweep).toHaveBeenCalledTimes(2);
    wakeup.wake();
    await vi.advanceTimersByTimeAsync(59_999);
    expect(sweep).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(sweep.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(JSON.stringify(report.mock.calls)).not.toContain("private");
    stop.abort();
    await running;
  });
});
