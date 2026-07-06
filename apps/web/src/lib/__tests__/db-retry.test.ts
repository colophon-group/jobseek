import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isRetryableError, withDbRetry } from "../db-retry";

/**
 * Coverage for the #2918 follow-up retry helper. The build at
 * 2026-05-09T15:41:49Z died on a single `read ECONNRESET` during
 * `_fetchCompanyBySlugFromPostgres`; this helper retries that class
 * of error and lets non-retryable errors propagate untouched.
 */

const _econnreset = (): Error => {
  const e = new Error("read ECONNRESET") as Error & { code: string };
  e.code = "ECONNRESET";
  return e;
};

describe("isRetryableError", () => {
  it("matches by Node `code` (ECONNRESET)", () => {
    expect(isRetryableError(_econnreset())).toBe(true);
  });

  it.each(["ETIMEDOUT", "ECONNREFUSED", "EPIPE"])(
    "matches by Node `code` (%s)",
    (code) => {
      const e = new Error("net") as Error & { code: string };
      e.code = code;
      expect(isRetryableError(e)).toBe(true);
    },
  );

  it("matches Supabase pooler restart message", () => {
    const e = new Error(
      "terminating connection due to administrator command",
    );
    expect(isRetryableError(e)).toBe(true);
  });

  it("matches postgres.js 'Connection terminated unexpectedly'", () => {
    expect(isRetryableError(new Error("Connection terminated unexpectedly")))
      .toBe(true);
  });

  it("recurses into `cause` (postgres.js wraps the network error)", () => {
    const inner = _econnreset();
    const outer = new Error("Failed query: SELECT …");
    (outer as Error & { cause: unknown }).cause = inner;
    expect(isRetryableError(outer)).toBe(true);
  });

  it("does NOT match unrelated Postgres errors (syntax, constraint)", () => {
    expect(isRetryableError(new Error("syntax error at or near \"FROM\""))).toBe(
      false,
    );
    expect(
      isRetryableError(
        new Error("duplicate key value violates unique constraint"),
      ),
    ).toBe(false);
  });

  it("returns false for non-error inputs", () => {
    expect(isRetryableError(null)).toBe(false);
    expect(isRetryableError(undefined)).toBe(false);
    expect(isRetryableError("string")).toBe(false);
    expect(isRetryableError(42)).toBe(false);
  });
});

describe("withDbRetry", () => {
  // Pin Math.random so the jitter component is deterministic in the
  // sleep-arg assertion. 0 jitter keeps the math obvious: each delay
  // equals the base.
  beforeEach(() => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the result on the first attempt without retrying", async () => {
    const fn = vi.fn().mockResolvedValue("ok");
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withDbRetry(fn, { sleep });

    expect(out).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("retries once after ECONNRESET, succeeds on second attempt", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce("ok");
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withDbRetry(fn, { sleep });

    expect(out).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(2);
    // First retry waits 200ms (base) + 0 jitter = 200ms
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(200);
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it("exhausts 3 attempts on persistent ECONNRESET, throws the last error", async () => {
    const finalErr = _econnreset();
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockRejectedValueOnce(_econnreset())
      .mockRejectedValueOnce(finalErr);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(withDbRetry(fn, { sleep })).rejects.toBe(finalErr);

    expect(fn).toHaveBeenCalledTimes(3);
    // 2 sleeps between 3 attempts: 200ms then 400ms (jitter pinned to 0)
    expect(sleep).toHaveBeenNthCalledWith(1, 200);
    expect(sleep).toHaveBeenNthCalledWith(2, 400);
    // No log on the final failure (we don't tell observability "retrying"
    // when we're not retrying); only attempts 1 and 2 log.
    expect(console.warn).toHaveBeenCalledTimes(2);
  });

  it("does NOT retry on non-retryable errors (syntax error)", async () => {
    const syntaxErr = new Error("syntax error at or near \"FROM\"");
    const fn = vi.fn().mockRejectedValue(syntaxErr);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(withDbRetry(fn, { sleep })).rejects.toBe(syntaxErr);

    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("does NOT retry on constraint violations", async () => {
    const constraintErr = new Error(
      "duplicate key value violates unique constraint \"company_slug_key\"",
    );
    const fn = vi.fn().mockRejectedValue(constraintErr);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(withDbRetry(fn, { sleep })).rejects.toBe(constraintErr);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("uses backoff schedule [200, 400, 800] by default", async () => {
    // 4 attempts forces all 3 sleep windows to fire in sequence.
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockRejectedValueOnce(_econnreset())
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce("ok");
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withDbRetry(fn, { sleep, attempts: 4 });

    expect(out).toBe("ok");
    expect(sleep).toHaveBeenNthCalledWith(1, 200);
    expect(sleep).toHaveBeenNthCalledWith(2, 400);
    expect(sleep).toHaveBeenNthCalledWith(3, 800);
  });

  it("adds jitter on top of the base delay (Math.random pinned high)", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.99); // → floor(0.99 * 101) = 99
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce("ok");
    const sleep = vi.fn().mockResolvedValue(undefined);

    await withDbRetry(fn, { sleep });

    expect(sleep).toHaveBeenCalledWith(200 + 99);
  });

  it("respects fake timers (sleep helper is overridable)", async () => {
    // Assert the contract: callers can pass `sleep` (we use it under
    // vi.useFakeTimers in the suite). This test pins fake timers,
    // hands withDbRetry a real-ish sleep that resolves on tick, and
    // verifies the loop awaits each sleep.
    vi.useFakeTimers();
    try {
      const fn = vi
        .fn()
        .mockRejectedValueOnce(_econnreset())
        .mockResolvedValueOnce("ok");
      let sleeps = 0;
      const sleep = (_ms: number): Promise<void> => {
        sleeps += 1;
        return Promise.resolve();
      };

      const out = await withDbRetry(fn, { sleep });
      expect(out).toBe("ok");
      expect(sleeps).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("logs the label so observability can tell call sites apart", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce("ok");
    const sleep = vi.fn().mockResolvedValue(undefined);

    await withDbRetry(fn, { sleep, label: "companyBySlug[chevron]" });

    const warnArg = (console.warn as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(warnArg).toContain("companyBySlug[chevron]");
    expect(warnArg).toContain("ECONNRESET");
  });
});
