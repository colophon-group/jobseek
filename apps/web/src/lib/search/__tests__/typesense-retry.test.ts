import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isRetryableError, withTypesenseRetry } from "../typesense-retry";

/**
 * Coverage for the #3008 cold-start retry helper. A cold Vercel
 * function instance opening a fresh TLS connection to Typesense over
 * the Cloudflare tunnel can hit a transient ECONNRESET / read timeout
 * during the first roundtrip; this helper retries that class with
 * exponential backoff and lets the deterministic errors (auth, 4xx,
 * schema) propagate untouched.
 */

const _econnreset = (): Error => {
  const e = new Error("read ECONNRESET") as Error & { code: string };
  e.code = "ECONNRESET";
  return e;
};

const _httpStatus = (status: number, message = "Server error"): Error => {
  const e = new Error(message) as Error & { httpStatus: number };
  e.httpStatus = status;
  return e;
};

describe("isRetryableError", () => {
  it("matches by Node `code` (ECONNRESET)", () => {
    expect(isRetryableError(_econnreset())).toBe(true);
  });

  it.each(["ETIMEDOUT", "ECONNREFUSED", "EPIPE", "ENOTFOUND", "ECONNABORTED", "EAI_AGAIN"])(
    "matches by Node `code` (%s)",
    (code) => {
      const e = new Error("net") as Error & { code: string };
      e.code = code;
      expect(isRetryableError(e)).toBe(true);
    },
  );

  it.each([502, 503, 504])(
    "matches by HTTP status (%i) — Typesense gateway/unavailable/timeout",
    (status) => {
      expect(isRetryableError(_httpStatus(status))).toBe(true);
    },
  );

  it("matches axios 'request timed out' message", () => {
    expect(isRetryableError(new Error("Request timed out"))).toBe(true);
  });

  it("matches 'socket hang up' (transient socket close mid-request)", () => {
    expect(isRetryableError(new Error("socket hang up"))).toBe(true);
  });

  it("matches 'connection reset' (TCP RST during TLS handshake)", () => {
    expect(isRetryableError(new Error("Connection reset by peer"))).toBe(true);
  });

  it("matches 'service unavailable' (Typesense returns 503 during boot)", () => {
    expect(isRetryableError(new Error("Service Unavailable"))).toBe(true);
  });

  it("recurses into `cause` (axios wraps the underlying network error)", () => {
    const inner = _econnreset();
    const outer = new Error("Failed search query");
    (outer as Error & { cause: unknown }).cause = inner;
    expect(isRetryableError(outer)).toBe(true);
  });

  it("does NOT match deterministic 4xx errors (auth, schema)", () => {
    expect(isRetryableError(_httpStatus(401, "Unauthorized"))).toBe(false);
    expect(isRetryableError(_httpStatus(403, "Forbidden"))).toBe(false);
    expect(isRetryableError(_httpStatus(404, "Not Found"))).toBe(false);
    expect(isRetryableError(_httpStatus(400, "Bad Parameter"))).toBe(false);
  });

  it("does NOT match arbitrary errors", () => {
    expect(isRetryableError(new Error("collection 'job_posting' has 0 fields"))).toBe(false);
    expect(isRetryableError(new Error("invalid filter expression"))).toBe(false);
  });

  it("returns false for non-error inputs", () => {
    expect(isRetryableError(null)).toBe(false);
    expect(isRetryableError(undefined)).toBe(false);
    expect(isRetryableError("string")).toBe(false);
    expect(isRetryableError(42)).toBe(false);
  });
});

describe("withTypesenseRetry", () => {
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
    const fn = vi.fn().mockResolvedValue({ hits: [] });
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withTypesenseRetry(fn, { sleep });

    expect(out).toEqual({ hits: [] });
    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("retries once after ECONNRESET, succeeds on second attempt", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce({ hits: [{ id: "1" }] });
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withTypesenseRetry(fn, { sleep });

    expect(out).toEqual({ hits: [{ id: "1" }] });
    expect(fn).toHaveBeenCalledTimes(2);
    // First retry waits 200ms (base) + 0 jitter = 200ms
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(200);
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it("retries on HTTP 503 (Typesense boot)", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_httpStatus(503))
      .mockResolvedValueOnce({ hits: [] });
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withTypesenseRetry(fn, { sleep });

    expect(out).toEqual({ hits: [] });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("exhausts 3 attempts on persistent timeout, throws the last error", async () => {
    const finalErr = new Error("Request timed out");
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new Error("Request timed out"))
      .mockRejectedValueOnce(new Error("Request timed out"))
      .mockRejectedValueOnce(finalErr);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(withTypesenseRetry(fn, { sleep })).rejects.toBe(finalErr);

    expect(fn).toHaveBeenCalledTimes(3);
    // 2 sleeps between 3 attempts: 200ms then 400ms (jitter pinned to 0)
    expect(sleep).toHaveBeenNthCalledWith(1, 200);
    expect(sleep).toHaveBeenNthCalledWith(2, 400);
    // No log on the final failure (we don't tell observability "retrying"
    // when we're not retrying); only attempts 1 and 2 log.
    expect(console.warn).toHaveBeenCalledTimes(2);
  });

  it("does NOT retry on 4xx auth errors (401)", async () => {
    const authErr = _httpStatus(401, "Unauthorized");
    const fn = vi.fn().mockRejectedValue(authErr);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(withTypesenseRetry(fn, { sleep })).rejects.toBe(authErr);

    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("does NOT retry on 400 Bad Parameter (deterministic schema mismatch)", async () => {
    const schemaErr = _httpStatus(400, "Bad Parameter: filter_by");
    const fn = vi.fn().mockRejectedValue(schemaErr);
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(withTypesenseRetry(fn, { sleep })).rejects.toBe(schemaErr);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("uses backoff schedule [200, 400] by default", async () => {
    // 3 attempts forces both sleep windows to fire in sequence.
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce({ hits: [] });
    const sleep = vi.fn().mockResolvedValue(undefined);

    const out = await withTypesenseRetry(fn, { sleep });

    expect(out).toEqual({ hits: [] });
    expect(sleep).toHaveBeenNthCalledWith(1, 200);
    expect(sleep).toHaveBeenNthCalledWith(2, 400);
  });

  it("adds jitter on top of the base delay (Math.random pinned high)", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.99); // → floor(0.99 * 101) = 99
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce({ hits: [] });
    const sleep = vi.fn().mockResolvedValue(undefined);

    await withTypesenseRetry(fn, { sleep });

    expect(sleep).toHaveBeenCalledWith(200 + 99);
  });

  it("logs the label so observability can tell call sites apart", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(_econnreset())
      .mockResolvedValueOnce({ hits: [] });
    const sleep = vi.fn().mockResolvedValue(undefined);

    await withTypesenseRetry(fn, { sleep, label: "search" });

    const warnArg = (console.warn as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string;
    expect(warnArg).toContain("search");
    expect(warnArg).toContain("ECONNRESET");
  });
});
