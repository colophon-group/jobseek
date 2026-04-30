import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useMurmurRunStatus } from "../use-murmur-run-status";
import type {
  UseMurmurRunStatusOptions,
  UseMurmurRunStatusResult,
} from "../use-murmur-run-status";

interface ProbeProps {
  runId: string | null;
  options: UseMurmurRunStatusOptions;
  onChange: (r: UseMurmurRunStatusResult) => void;
}

function Probe({ runId, options, onChange }: ProbeProps) {
  const result = useMurmurRunStatus(runId, options);
  onChange(result);
  return null;
}

function mkResponse(
  status: number,
  data: Record<string, unknown>,
): Response {
  return new Response(JSON.stringify({ ok: status === 200, data }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("useMurmurRunStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("stays idle when runId is null", async () => {
    const seen: UseMurmurRunStatusResult[] = [];
    const fetchImpl = vi.fn();
    render(
      <Probe
        runId={null}
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => 0,
        }}
        onChange={(r) => seen.push(r)}
      />,
    );
    expect(seen.at(-1)?.state).toBe("idle");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("polls every 5s for the first 5 minutes (initial cadence)", async () => {
    const seen: UseMurmurRunStatusResult[] = [];
    let nowMs = 0;
    const fetchImpl = vi.fn(async () =>
      mkResponse(200, { status: "running", webhook_status: "pending" }),
    );

    render(
      <Probe
        runId="r_abc"
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => nowMs,
        }}
        onChange={(r) => seen.push(r)}
      />,
    );

    // First fetch happens on mount (tick 0).
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    // Advance 5s -> second fetch.
    await act(async () => {
      nowMs = 5_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);

    // Advance another 5s -> third fetch (still in initial cadence).
    await act(async () => {
      nowMs = 10_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetchImpl).toHaveBeenCalledTimes(3);

    expect(seen.at(-1)?.state).toBe("running");
    expect(seen.at(-1)?.webhookStatus).toBe("pending");
  });

  it("backs off to 30s after 5 minutes elapsed", async () => {
    let nowMs = 0;
    const fetchImpl = vi.fn(async () =>
      mkResponse(200, { status: "running", webhook_status: "pending" }),
    );
    render(
      <Probe
        runId="r_abc"
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => nowMs,
        }}
        onChange={() => {}}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    const initial = fetchImpl.mock.calls.length;

    // Jump just past the 5-minute boundary so the next scheduled tick is the
    // first "after backoff" decision point.
    await act(async () => {
      nowMs = 5 * 60 * 1000 + 1;
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 1);
    });

    const beforeBackoffCount = fetchImpl.mock.calls.length;
    expect(beforeBackoffCount).toBeGreaterThan(initial);

    // After the boundary, advancing 5s should NOT fire a new fetch (cadence
    // is now 30s).
    await act(async () => {
      nowMs += 5_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    // Allow one extra fetch from a request scheduled before the boundary.
    expect(fetchImpl.mock.calls.length).toBeLessThanOrEqual(
      beforeBackoffCount + 1,
    );

    // Advancing another 30s SHOULD fire one (the backoff cadence).
    await act(async () => {
      nowMs += 30_000;
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(fetchImpl.mock.calls.length).toBeGreaterThan(beforeBackoffCount);
  });

  it("transitions to completed when webhook_status flips to delivered", async () => {
    const seen: UseMurmurRunStatusResult[] = [];
    let call = 0;
    let nowMs = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      if (call < 3) {
        return mkResponse(200, {
          status: "running",
          webhook_status: "pending",
        });
      }
      return mkResponse(200, {
        status: "completed",
        webhook_status: "delivered",
        slug: "anthropic",
        company_id: "cmp-1",
      });
    });

    render(
      <Probe
        runId="r_abc"
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => nowMs,
        }}
        onChange={(r) => seen.push(r)}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      nowMs = 5_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await act(async () => {
      nowMs = 10_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });

    const final = seen.at(-1);
    expect(final?.state).toBe("completed");
    expect(final?.slug).toBe("anthropic");
    expect(final?.companyId).toBe("cmp-1");
    expect(final?.webhookStatus).toBe("delivered");

    // Once completed, no further fetches even if we advance time.
    const callsAtCompletion = fetchImpl.mock.calls.length;
    await act(async () => {
      nowMs += 60_000;
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchImpl.mock.calls.length).toBe(callsAtCompletion);
  });

  it("gives up after 30 minutes", async () => {
    const seen: UseMurmurRunStatusResult[] = [];
    let nowMs = 0;
    const fetchImpl = vi.fn(async () =>
      mkResponse(200, { status: "running", webhook_status: "pending" }),
    );
    render(
      <Probe
        runId="r_abc"
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => nowMs,
        }}
        onChange={(r) => seen.push(r)}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    // Walk forward in 1-minute chunks until we cross the 30-minute boundary.
    for (let m = 0; m <= 31; m++) {
      await act(async () => {
        nowMs += 60_000;
        await vi.advanceTimersByTimeAsync(60_000);
      });
    }
    expect(seen.at(-1)?.state).toBe("given_up");

    // After give-up, no further fetches.
    const callsAtGiveUp = fetchImpl.mock.calls.length;
    await act(async () => {
      nowMs += 60_000;
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchImpl.mock.calls.length).toBe(callsAtGiveUp);
  });

  it("aborts in-flight fetch and clears timer on unmount", async () => {
    let nowMs = 0;
    let lastSignal: AbortSignal | null = null;
    const fetchImpl = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      lastSignal = init?.signal ?? null;
      return mkResponse(200, {
        status: "running",
        webhook_status: "pending",
      });
    });
    const { unmount } = render(
      <Probe
        runId="r_abc"
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => nowMs,
        }}
        onChange={() => {}}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    unmount();
    expect(lastSignal).not.toBeNull();
    expect(lastSignal!.aborted).toBe(true);

    // No further fetch after unmount even when we advance the clock.
    const calls = fetchImpl.mock.calls.length;
    await act(async () => {
      nowMs += 5_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetchImpl.mock.calls.length).toBe(calls);
  });

  it("keeps polling on transient errors (5xx, network)", async () => {
    let nowMs = 0;
    let call = 0;
    const fetchImpl = vi.fn(async () => {
      call += 1;
      if (call === 1) throw new Error("network");
      if (call === 2) return mkResponse(503, {});
      return mkResponse(200, { status: "running", webhook_status: "pending" });
    });
    const seen: UseMurmurRunStatusResult[] = [];
    render(
      <Probe
        runId="r_abc"
        options={{
          fetchImpl: fetchImpl as unknown as typeof fetch,
          now: () => nowMs,
        }}
        onChange={(r) => seen.push(r)}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      nowMs = 5_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await act(async () => {
      nowMs = 10_000;
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(call).toBeGreaterThanOrEqual(3);
    expect(seen.at(-1)?.state).toBe("running");
  });
});
