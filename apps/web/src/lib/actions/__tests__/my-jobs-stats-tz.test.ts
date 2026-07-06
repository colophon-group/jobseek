import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3199 — Activity-heatmap TZ bug.
 *
 * Before the fix, `getMyJobsStats()` bucketed `saved_at` via
 * `to_char(saved_at, 'YYYY-MM-DD')` in Postgres's TZ (UTC on Supabase),
 * while the client built cell keys with `Date.getFullYear/Month/Date`
 * (browser TZ). A NYC user saving at 23:00 local time would see the
 * dot appear on the *next* day's cell (or fall outside the rendered
 * 52-week window).
 *
 * The fix:
 *   - server: bucket `saved_at AT TIME ZONE $tz` where `$tz` is the
 *     viewer's IANA timezone (passed from the client),
 *   - server: interpret `from`/`to` calendar-day filters at that same
 *     TZ's midnight rather than UTC midnight,
 *   - server: validate the supplied TZ against an IANA-shaped pattern
 *     and fall back to "UTC" otherwise — both as defense-in-depth and
 *     so older clients (no `tz` field) keep working.
 *
 * The cell grid on the client still uses browser-TZ `Date` accessors,
 * but with the server bucketing in the same TZ the day keys align.
 *
 * This spec asserts the SQL produced by `getMyJobsStats({ tz })` reaches
 * Postgres parameterised with the right TZ and the right shape, and
 * that malformed inputs are coerced to "UTC".
 */

vi.mock("server-only", () => ({}));

interface Captured {
  sqlObjects: unknown[];
}

const captured: Captured = { sqlObjects: [] };

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: vi.fn().mockResolvedValue("user-1"),
}));

vi.mock("@/lib/db-retry", () => ({
  withDbRetry: <T>(fn: () => Promise<T>) => fn(),
}));

vi.mock("@/db", () => ({
  db: {
    execute: vi.fn(async (sqlObj: unknown) => {
      captured.sqlObjects.push(sqlObj);
      return [];
    }),
  },
}));

/**
 * Drizzle's `sql\`...\`` tagged template stores its parts on
 * `queryChunks`. Empirically (against drizzle-orm v0.x at the
 * version pinned here), an interpolated *bare value* is left as a
 * raw chunk of its primitive type (string, number, ...) — Param
 * wrappers are produced by helpers like `sql.param()` but not by
 * direct `${value}` interpolation. SQL fragments are stored as
 * `StringChunk { value: string[] }` objects, and a nested `sql\`\``
 * shows up as another object with its own `queryChunks` array.
 *
 * This walker recurses into nested chunks, treats every primitive
 * encountered as a parameter, and concatenates string-array
 * `StringChunk.value` arrays as the SQL body. The output is good
 * enough to assert "this query contains `AT TIME ZONE` and was
 * parameterised with `America/New_York`".
 */
function flatten(
  sqlObj: unknown,
): { text: string; params: unknown[] } {
  const parts: string[] = [];
  const params: unknown[] = [];

  const walk = (obj: unknown): void => {
    const queryChunks = (obj as { queryChunks?: unknown[] }).queryChunks;
    if (!Array.isArray(queryChunks)) return;
    for (const chunk of queryChunks) {
      if (chunk === null || chunk === undefined) continue;
      const t = typeof chunk;
      if (t === "string") {
        params.push(chunk);
        parts.push("$?");
        continue;
      }
      if (t === "number" || t === "bigint" || t === "boolean") {
        params.push(chunk);
        parts.push("$?");
        continue;
      }
      if (t === "object") {
        const c = chunk as {
          value?: unknown;
          encoder?: unknown;
          queryChunks?: unknown[];
        };
        if (Array.isArray(c.queryChunks)) {
          walk(c);
          continue;
        }
        // Param wrapper: { value, encoder }
        if (c.encoder !== undefined) {
          params.push(c.value);
          parts.push("$?");
          continue;
        }
        // StringChunk: { value: string[] }
        if (Array.isArray(c.value)) {
          parts.push((c.value as string[]).join(""));
          continue;
        }
        if (typeof c.value === "string") {
          parts.push(c.value);
          continue;
        }
      }
    }
  };
  walk(sqlObj);
  return { text: parts.join(""), params };
}

describe("#3199 — getMyJobsStats TZ-aware day bucketing", () => {
  beforeEach(() => {
    captured.sqlObjects = [];
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("buckets activity in the viewer-supplied IANA TZ", async () => {
    const { getMyJobsStats } = await import("@/lib/actions/my-jobs-stats");
    await getMyJobsStats({ tz: "America/New_York" });

    // Two queries: funnel + activity. The activity query is the one
    // that references `to_char(... 'YYYY-MM-DD')`.
    const activity = captured.sqlObjects
      .map(flatten)
      .find((q) => q.text.includes("to_char"));
    expect(activity).toBeDefined();

    // The SQL must apply AT TIME ZONE before to_char so the bucket
    // is in viewer-local time, not Postgres-server time.
    expect(activity!.text).toMatch(/saved_at\s+AT TIME ZONE/);
    expect(activity!.text).toMatch(/to_char\(\s*saved_at AT TIME ZONE/);

    // And the TZ must be passed in as a parameter (drizzle codegen
    // replaces interpolated values with $? in the flattened text).
    expect(activity!.params).toContain("America/New_York");
  });

  it("interprets from/to date filters at the viewer's local midnight", async () => {
    const { getMyJobsStats } = await import("@/lib/actions/my-jobs-stats");
    await getMyJobsStats({
      from: "2026-05-13",
      to: "2026-05-14",
      tz: "America/New_York",
    });

    const funnel = captured.sqlObjects
      .map(flatten)
      .find((q) => q.text.includes("sj.saved_at"));
    expect(funnel).toBeDefined();

    // Both bounds must use AT TIME ZONE so 2026-05-13 means midnight
    // in NYC, not UTC.
    expect(funnel!.text).toMatch(/saved_at\s+>=\s+\(.*AT TIME ZONE/);
    expect(funnel!.text).toMatch(/saved_at\s+<\s+\(.*AT TIME ZONE/);

    // The TZ parameter must appear (at least once per bound).
    const tzAppearances = funnel!.params.filter(
      (p) => p === "America/New_York",
    ).length;
    expect(tzAppearances).toBeGreaterThanOrEqual(2);

    // The from/to dates must also be parameters.
    expect(funnel!.params).toContain("2026-05-13");
    expect(funnel!.params).toContain("2026-05-14");
  });

  it("falls back to UTC when no tz is supplied (preserves legacy behaviour)", async () => {
    const { getMyJobsStats } = await import("@/lib/actions/my-jobs-stats");
    await getMyJobsStats({});

    const activity = captured.sqlObjects
      .map(flatten)
      .find((q) => q.text.includes("to_char"));
    expect(activity).toBeDefined();
    expect(activity!.params).toContain("UTC");
  });

  it("rejects malformed TZ inputs and falls back to UTC", async () => {
    const { getMyJobsStats } = await import("@/lib/actions/my-jobs-stats");

    const malformed = [
      "'; DROP TABLE saved_job; --",
      "UTC; SELECT 1",
      "America/New_York'",
      "../etc/passwd",
      " ",
      "a".repeat(100),
    ];

    for (const tz of malformed) {
      captured.sqlObjects = [];
      await getMyJobsStats({ tz });
      const activity = captured.sqlObjects
        .map(flatten)
        .find((q) => q.text.includes("to_char"));
      expect(activity, `tz=${tz}`).toBeDefined();
      // The malformed input must NEVER reach the SQL parameter slot.
      expect(activity!.params).not.toContain(tz);
      // Fallback is UTC.
      expect(activity!.params).toContain("UTC");
    }
  });

  it("accepts canonical IANA names with subzones", async () => {
    const { getMyJobsStats } = await import("@/lib/actions/my-jobs-stats");

    const valid = [
      "Europe/Zurich",
      "America/Argentina/Buenos_Aires",
      "Asia/Ho_Chi_Minh",
      "Etc/GMT+5",
      "UTC",
    ];

    for (const tz of valid) {
      captured.sqlObjects = [];
      await getMyJobsStats({ tz });
      const activity = captured.sqlObjects
        .map(flatten)
        .find((q) => q.text.includes("to_char"));
      expect(activity, `tz=${tz}`).toBeDefined();
      expect(activity!.params).toContain(tz);
    }
  });
});
