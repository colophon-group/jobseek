import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3165 — `recordPasswordResetRequest` cooldown gate is TOCTOU.
 *
 * Before the fix, `recordPasswordResetRequest` ran two statements:
 *   1. `SELECT last_password_reset_at FROM user_preferences …`
 *      (via `getPasswordResetCooldown`)
 *   2. `INSERT … ON CONFLICT (user_id) DO UPDATE SET
 *       last_password_reset_at = now()`
 *
 * Two concurrent callers (two browser tabs of the same user
 * double-clicking "Forgot password?") both observed the same
 * `last_password_reset_at` (NULL on first ever, or stale after
 * cooldown expiry), both reached step 2, both committed the upsert,
 * and the user received two reset emails — even though the UI
 * promised a 60-second cooldown.
 *
 * The fix collapses both into a single
 * `INSERT … ON CONFLICT (user_id) DO UPDATE … WHERE
 *   user_preferences.last_password_reset_at IS NULL
 *     OR user_preferences.last_password_reset_at
 *          < now() - make_interval(secs => 60)
 *  RETURNING 1 AS updated`
 * statement. The `ON CONFLICT … WHERE` predicate runs atomically with
 * the upsert, and Postgres serialises concurrent upserts on the same
 * conflict target. Exactly one of two racing callers sees a row in
 * `RETURNING`; the other sees zero rows and surfaces a cooldown to
 * the caller.
 *
 * Test strategy
 * -------------
 * We replace `@/db` with an in-memory mock whose `execute` callback
 * mirrors the relevant postgres semantics:
 *   - `execute(sql)` for the upsert is atomic per call: a single
 *     microtask hop simulates the roundtrip, then the conflict check +
 *     WHERE check + write happens synchronously inside the same micro
 *     window. Two parallel callers interleave on the await; the second
 *     to land sees the first's write (Postgres's serialised
 *     ON CONFLICT semantics) and the WHERE predicate now evaluates to
 *     false, so it returns 0 rows.
 *   - `select().from().where().limit()` returns the current
 *     `lastPasswordResetAt` for the user, used by the read-back path
 *     when the upsert was skipped.
 *
 * Calibration: a second describe-block re-implements the legacy
 * SELECT-then-INSERT shape against the SAME mock and asserts that two
 * concurrent callers BOTH commit their writes — exactly the bug the
 * fix repairs. Without this calibration the test could trivially pass
 * for both shapes.
 */

vi.mock("server-only", () => ({}));

interface UserPreferencesRow {
  user_id: string;
  theme: string;
  locale: string;
  cookie_consent: boolean;
  last_password_reset_at: Date | null;
  updated_at: Date;
}

const mocks = vi.hoisted(() => {
  // Verbatim row store keyed by user_id (UNIQUE in production).
  const userPreferencesTable: UserPreferencesRow[] = [];
  // The mock execute() uses `Date.now()` for its `now()` simulation so
  // that the production action's `getPasswordResetCooldown()` read-back
  // (which also uses `Date.now()`) sees the same wall-clock the mock
  // wrote with. Specs synchronise both by calling `vi.useFakeTimers()`
  // and `vi.setSystemTime(...)`.
  const nowMs = (): number => Date.now();
  const nowDate = (): Date => new Date(nowMs());

  // Per-call upsert counter — lets specs verify exactly one call wrote
  // a fresh `last_password_reset_at` value under a concurrent race.
  let upsertWrites = 0;

  // ---- db.execute(sql) — atomic INSERT … ON CONFLICT … WHERE ------
  //
  // The action only invokes `db.execute` for the upsert path. Sniff
  // the userId out of the tagged-template `sql` call by walking its
  // `queryChunks`. The cooldown-seconds parameter is also a sql `${}`
  // expression; we capture both in encounter order. The body is fully
  // synchronous from here on, mirroring postgres's single-statement
  // atomicity (the row lock acquired on the conflict target serialises
  // any concurrent upsert against the same key).
  const dbExecute = async (sqlObj: unknown): Promise<unknown[]> => {
    const params = extractSqlParams(sqlObj);
    // The action interpolates ${userId} (string) and
    // ${PASSWORD_RESET_COOLDOWN_SECONDS} (number). Pick by type so we
    // don't depend on the exact order if the action's SQL gets edited.
    const userId = params.find((p) => typeof p === "string") as
      | string
      | undefined;
    const cooldownSeconds = params.find((p) => typeof p === "number") as
      | number
      | undefined;

    if (!userId || cooldownSeconds === undefined) {
      throw new Error(
        "password-reset-race mock: expected userId (string) and cooldownSeconds (number) params",
      );
    }

    // Yield once so two parallel callers' execute bodies interleave
    // before the conflict check, reproducing the same window postgres
    // sees under concurrent statements.
    await Promise.resolve();

    const existingIdx = userPreferencesTable.findIndex(
      (r) => r.user_id === userId,
    );

    if (existingIdx === -1) {
      // INSERT branch — no row yet, the WHERE on the DO UPDATE is
      // irrelevant. Insert and emit the RETURNING row.
      const row: UserPreferencesRow = {
        user_id: userId,
        theme: "light",
        locale: "en",
        cookie_consent: false,
        last_password_reset_at: nowDate(),
        updated_at: nowDate(),
      };
      userPreferencesTable.push(row);
      upsertWrites += 1;
      return [{ updated: 1 }];
    }

    // ON CONFLICT DO UPDATE branch — evaluate the WHERE predicate
    // atomically against the existing row.
    const existing = userPreferencesTable[existingIdx];
    const last = existing.last_password_reset_at;
    const cooldownElapsed =
      last === null
      || last.getTime() < nowMs() - cooldownSeconds * 1000;

    if (!cooldownElapsed) {
      // WHERE failed → no row written, RETURNING emits zero rows.
      return [];
    }

    existing.last_password_reset_at = nowDate();
    existing.updated_at = nowDate();
    upsertWrites += 1;
    return [{ updated: 1 }];
  };

  // ---- db.select(...).from(...).where(...).limit(...) -------------
  //
  // Used by `getPasswordResetCooldown` (read-only helper) AND by the
  // production action's read-back path when the upsert was skipped.
  const dbSelect = () => {
    let userIdFilter: string | undefined;
    const chain: Record<string, unknown> = {
      from: () => chain,
      where: (clause: unknown) => {
        const params = extractParams(clause);
        const strs = params.filter((p): p is string => typeof p === "string");
        if (strs.length >= 1) userIdFilter = strs[0];
        return chain;
      },
      limit: async () => {
        await Promise.resolve();
        if (!userIdFilter) return [];
        const row = userPreferencesTable.find(
          (r) => r.user_id === userIdFilter,
        );
        if (!row) return [];
        return [{ lastPasswordResetAt: row.last_password_reset_at }];
      },
    };
    return chain;
  };

  return {
    userPreferencesTable,
    getUpsertWrites: () => upsertWrites,
    reset: () => {
      userPreferencesTable.length = 0;
      upsertWrites = 0;
    },
    getSession: vi.fn(),
    dbExecute,
    dbSelect,
  };
});

/**
 * Drizzle's `sql\`...\`` tagged template stores its parts on
 * `queryChunks: Array<Param | StringChunk>`. The interpolated values
 * appear wrapped in `{ value, encoder }` (Param); the SQL fragments are
 * wrapped in `{ value: string[] }` (StringChunk) with no `.encoder`.
 * Walk the chunks and pick out the Param values.
 */
function extractSqlParams(sqlObj: unknown): unknown[] {
  const params: unknown[] = [];
  const queryChunks = (sqlObj as { queryChunks?: unknown[] }).queryChunks;
  if (!Array.isArray(queryChunks)) return params;
  for (const chunk of queryChunks) {
    if (chunk === null || chunk === undefined) continue;
    const t = typeof chunk;
    if (
      t === "string"
      || t === "number"
      || t === "bigint"
      || t === "boolean"
    ) {
      params.push(chunk);
    } else if (t === "object") {
      const v = (chunk as { value?: unknown; encoder?: unknown }).value;
      const hasEncoder
        = (chunk as { encoder?: unknown }).encoder !== undefined;
      if (hasEncoder && v !== undefined) params.push(v);
    }
  }
  return params;
}

/**
 * For drizzle SQL nodes produced by `eq`/`and`. They store params in
 * `.queryChunks` and may nest other SQL nodes inside. Recurse with a
 * depth cap to pluck primitive Param values in encounter order.
 * Mirrors the helper used in `watchlist-slug-race.test.ts`.
 */
function extractParams(node: unknown, depth = 0): unknown[] {
  if (depth > 8 || node === null || node === undefined) return [];
  if (typeof node !== "object") return [];
  const acc: unknown[] = [];
  const queryChunks = (node as { queryChunks?: unknown[] }).queryChunks;
  if (Array.isArray(queryChunks)) {
    for (const chunk of queryChunks) {
      if (chunk === null || chunk === undefined) continue;
      const t = typeof chunk;
      if (
        t === "string"
        || t === "number"
        || t === "bigint"
        || t === "boolean"
      ) {
        acc.push(chunk);
        continue;
      }
      if (t === "object") {
        const c = chunk as { value?: unknown; encoder?: unknown };
        const hasEncoder = c.encoder !== undefined;
        if (hasEncoder && c.value !== undefined) {
          acc.push(c.value);
          continue;
        }
        acc.push(...extractParams(chunk, depth + 1));
      }
    }
  }
  const direct = (node as { value?: unknown }).value;
  if (direct !== undefined && typeof direct !== "object" && acc.length === 0) {
    acc.push(direct);
  }
  return acc;
}

vi.mock("@/lib/sessionCache", () => ({
  getSession: mocks.getSession,
}));

vi.mock("@/db", () => ({
  db: {
    select: () => mocks.dbSelect(),
    execute: (sqlObj: unknown) => mocks.dbExecute(sqlObj),
  },
}));

beforeEach(() => {
  vi.resetModules();
  mocks.reset();
  mocks.getSession.mockResolvedValue({ user: { id: "user-1" } });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("#3165 — recordPasswordResetRequest TOCTOU", () => {
  it("first-ever call (no row exists) succeeds and writes the row", async () => {
    const { recordPasswordResetRequest } = await import(
      "@/lib/actions/preferences"
    );

    const r = await recordPasswordResetRequest();

    // Fresh write — no `cooldown`, no `error`.
    expect(r).toEqual({});
    expect(mocks.userPreferencesTable).toHaveLength(1);
    expect(mocks.userPreferencesTable[0]?.user_id).toBe("user-1");
    expect(
      mocks.userPreferencesTable[0]?.last_password_reset_at,
    ).toBeInstanceOf(Date);
    expect(mocks.getUpsertWrites()).toBe(1);
  }, 15_000);

  it("returns cooldown when called twice within 60s (sequential)", async () => {
    const { recordPasswordResetRequest } = await import(
      "@/lib/actions/preferences"
    );

    // Freeze JS+mock clock at a known point so the second call lands
    // ~1ms after the first and the cooldown predicate fails cleanly.
    // The action's read-back path uses Date.now(); the mock's upsert
    // simulation also uses Date.now(); fake timers synchronise both.
    const t0 = new Date("2026-05-14T12:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(t0);

    const r1 = await recordPasswordResetRequest();
    expect(r1).toEqual({});
    expect(mocks.getUpsertWrites()).toBe(1);

    // Advance 1s — still well inside the 60s window.
    vi.setSystemTime(new Date(t0.getTime() + 1000));
    const r2 = await recordPasswordResetRequest();
    expect(r2.error).toBeUndefined();
    expect(r2.cooldown).toBeGreaterThanOrEqual(1);
    expect(r2.cooldown).toBeLessThanOrEqual(60);
    expect(mocks.getUpsertWrites()).toBe(1); // no second write
  });

  it("succeeds again once 60s have elapsed", async () => {
    const { recordPasswordResetRequest } = await import(
      "@/lib/actions/preferences"
    );

    const t0 = new Date("2026-05-14T12:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(t0);

    const r1 = await recordPasswordResetRequest();
    expect(r1).toEqual({});
    expect(mocks.getUpsertWrites()).toBe(1);

    // Advance JS+mock clock past the 60-second cooldown.
    vi.setSystemTime(new Date(t0.getTime() + 61_000));
    const r2 = await recordPasswordResetRequest();
    expect(r2).toEqual({});
    expect(mocks.getUpsertWrites()).toBe(2);
  });

  /**
   * CRITICAL: this is the race the fix exists to prevent. Two
   * concurrent callers on a fresh row both reach `db.execute` at the
   * same time. On the BUGGY shape (calibration block below) both
   * SELECTs return NULL and both INSERTs succeed → two emails. On
   * the FIXED shape the second caller's upsert serialises behind the
   * first, the WHERE predicate evaluates against the just-written
   * row, and the second caller sees zero rows returned → returns a
   * cooldown instead of {}.
   */
  it("two concurrent callers (first-ever): exactly one succeeds, one returns cooldown", async () => {
    const { recordPasswordResetRequest } = await import(
      "@/lib/actions/preferences"
    );

    // Freeze JS+mock clock so the cooldown predicate is deterministic
    // (both upserts evaluate `now()` against the same boundary).
    const t0 = new Date("2026-05-14T12:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(t0);

    const [a, b] = await Promise.all([
      recordPasswordResetRequest(),
      recordPasswordResetRequest(),
    ]);

    // Exactly one fresh write — the other caller sees a cooldown.
    const successes = [a, b].filter((r) => Object.keys(r).length === 0);
    const cooldowns = [a, b].filter(
      (r) => typeof r.cooldown === "number" && !r.error,
    );
    expect(successes).toHaveLength(1);
    expect(cooldowns).toHaveLength(1);
    expect(cooldowns[0]?.cooldown).toBeGreaterThanOrEqual(1);
    expect(cooldowns[0]?.cooldown).toBeLessThanOrEqual(60);

    // The crucial post-condition: exactly one row in the table and
    // exactly one upsert write happened. Two writes would mean two
    // password reset emails would fire in production.
    expect(mocks.userPreferencesTable).toHaveLength(1);
    expect(mocks.getUpsertWrites()).toBe(1);
  });

  it("two concurrent callers (cooldown active): both return cooldown, no extra write", async () => {
    const { recordPasswordResetRequest } = await import(
      "@/lib/actions/preferences"
    );

    // Seed a recent write inside the cooldown window.
    const t0 = new Date("2026-05-14T12:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(t0);
    await recordPasswordResetRequest();
    expect(mocks.getUpsertWrites()).toBe(1);

    // 5 seconds later, two concurrent callers both attempt a reset.
    vi.setSystemTime(new Date(t0.getTime() + 5000));

    const [a, b] = await Promise.all([
      recordPasswordResetRequest(),
      recordPasswordResetRequest(),
    ]);

    expect(a.cooldown).toBeGreaterThanOrEqual(1);
    expect(b.cooldown).toBeGreaterThanOrEqual(1);
    expect(a.cooldown).toBeLessThanOrEqual(60);
    expect(b.cooldown).toBeLessThanOrEqual(60);
    // Still only the original write — neither racing call upserted.
    expect(mocks.getUpsertWrites()).toBe(1);
  });

  it("unauthenticated caller returns { error } without touching the DB", async () => {
    mocks.getSession.mockResolvedValueOnce(null);
    const { recordPasswordResetRequest } = await import(
      "@/lib/actions/preferences"
    );

    const r = await recordPasswordResetRequest();
    expect(r.error).toBe("not_authenticated");
    expect(mocks.userPreferencesTable).toHaveLength(0);
    expect(mocks.getUpsertWrites()).toBe(0);
  });
});

describe("#3165 — getPasswordResetCooldown (read-only helper)", () => {
  it("returns 0 when no row exists", async () => {
    const { getPasswordResetCooldown } = await import(
      "@/lib/actions/preferences"
    );
    expect(await getPasswordResetCooldown()).toBe(0);
  });

  it("returns 0 when the last reset is older than the cooldown window", async () => {
    mocks.userPreferencesTable.push({
      user_id: "user-1",
      theme: "light",
      locale: "en",
      cookie_consent: false,
      last_password_reset_at: new Date(Date.now() - 120_000),
      updated_at: new Date(),
    });
    const { getPasswordResetCooldown } = await import(
      "@/lib/actions/preferences"
    );
    expect(await getPasswordResetCooldown()).toBe(0);
  });

  it("returns the remaining cooldown when inside the window", async () => {
    mocks.userPreferencesTable.push({
      user_id: "user-1",
      theme: "light",
      locale: "en",
      cookie_consent: false,
      last_password_reset_at: new Date(Date.now() - 10_000),
      updated_at: new Date(),
    });
    const { getPasswordResetCooldown } = await import(
      "@/lib/actions/preferences"
    );
    const remaining = await getPasswordResetCooldown();
    // 60 - elapsed; elapsed is ~10s, allow ±1s for test scheduling
    // jitter between the seed write and the read.
    expect(remaining).toBeGreaterThanOrEqual(49);
    expect(remaining).toBeLessThanOrEqual(50);
  });

  it("returns 0 for unauthenticated viewers", async () => {
    mocks.getSession.mockResolvedValueOnce(null);
    const { getPasswordResetCooldown } = await import(
      "@/lib/actions/preferences"
    );
    expect(await getPasswordResetCooldown()).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Calibration: the legacy SELECT-then-INSERT algorithm.
//
// Re-implement the pre-fix `recordPasswordResetRequest` shape directly
// against the SAME mock harness, and assert that under Promise.all on
// a fresh row both callers commit their writes. Without this
// calibration the test could trivially pass for both shapes (a mock
// that always-serialises would make even the buggy code look fixed).
// ─────────────────────────────────────────────────────────────────────

describe("#3165 — calibration: legacy SELECT-then-INSERT both-commit race", () => {
  it("buggy SELECT-then-INSERT shape lets two concurrent callers both write", async () => {
    const PASSWORD_RESET_COOLDOWN_SECONDS = 60;
    // Freeze JS+mock clock so both callers evaluate the same boundary.
    const t0 = new Date("2026-05-14T12:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(t0);

    // Mirror the pre-#3165 shape:
    //   1. SELECT last_password_reset_at → compute remaining cooldown
    //   2. If remaining > 0 → return { cooldown }
    //   3. Else → upsert with last_password_reset_at = now()
    const buggyRecord = async (
      userId: string,
    ): Promise<{ cooldown?: number }> => {
      // SELECT step — open the race window with a microtask hop so
      // two parallel callers' SELECTs both run before either INSERT.
      const existing = mocks.userPreferencesTable.find(
        (r) => r.user_id === userId,
      );
      await Promise.resolve();
      const last = existing?.last_password_reset_at ?? null;
      if (last) {
        const elapsedSec = Math.floor((Date.now() - last.getTime()) / 1000);
        const remaining = Math.max(
          0,
          PASSWORD_RESET_COOLDOWN_SECONDS - elapsedSec,
        );
        if (remaining > 0) return { cooldown: remaining };
      }

      // Upsert step — racy. Two parallel callers both reach this with
      // `last === null` on the first-ever request and both succeed.
      await Promise.resolve();
      const idx = mocks.userPreferencesTable.findIndex(
        (r) => r.user_id === userId,
      );
      if (idx === -1) {
        mocks.userPreferencesTable.push({
          user_id: userId,
          theme: "light",
          locale: "en",
          cookie_consent: false,
          last_password_reset_at: new Date(),
          updated_at: new Date(),
        });
      } else {
        mocks.userPreferencesTable[idx].last_password_reset_at = new Date();
        mocks.userPreferencesTable[idx].updated_at = new Date();
      }
      return {};
    };

    // Reset to a clean state (the previous describe-blocks may have
    // run before; vitest's `beforeEach` clears the table but not the
    // serverNow we just set).
    mocks.userPreferencesTable.length = 0;

    const [a, b] = await Promise.all([
      buggyRecord("user-1"),
      buggyRecord("user-1"),
    ]);

    // BOTH callers got `{}` — both would have fired a reset email.
    // The fix flips this to (exactly one `{}`, exactly one cooldown).
    expect(a).toEqual({});
    expect(b).toEqual({});
    // And the table got two writes' worth of state — the second one
    // overwrites the first's `last_password_reset_at`, but both
    // emails would have fired in production.
    expect(mocks.userPreferencesTable).toHaveLength(1);
  });
});
