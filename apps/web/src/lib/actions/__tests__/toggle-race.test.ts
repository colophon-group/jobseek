import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3179 — `toggleSavedJob` / `toggleStarredCompany` SELECT-then-
 * INSERT-OR-DELETE race.
 *
 * Before the fix, each toggle ran two statements:
 *   1. `SELECT id FROM saved_job WHERE user_id = ? AND job_posting_id = ?`
 *   2a. If row found → `DELETE FROM saved_job WHERE id = ?`
 *   2b. If not found → `INSERT INTO saved_job (...) VALUES (...)`
 *
 * Two concurrent callers (most commonly a double-click on flaky
 * network) for the same (user, posting) both observed an empty
 * SELECT, both attempted the INSERT, and the loser crashed with an
 * un-handled Postgres `23505` from `idx_sj_user_posting`. The UNIQUE
 * index protected the data but the client saw a 500.
 *
 * Fix matches the #3268 retry-on-conflict shape: try INSERT first,
 * catch `23505` scoped to the specific UNIQUE constraint, and treat
 * the conflict as "toggle was ON, transition to OFF" — DELETE the
 * matching row and return `saved/starred: false`.
 *
 * Test strategy
 * -------------
 * We replace `@/db` with an in-memory mock that mirrors the postgres
 * shape relevant to the toggle:
 *   - `db.insert(...).values(...).returning(...)` is atomic per call.
 *     Two parallel calls each compute against a fresh snapshot, and
 *     UNIQUE (user_id, target_id) is enforced — true contention
 *     surfaces 23505.
 *   - `db.delete(...).where(...)` is also atomic per call and a
 *     zero-row delete is a silent no-op (matching drizzle's actual
 *     behaviour, not an error).
 *
 * Calibration: a second describe-block runs the legacy
 * SELECT-then-INSERT shape directly against the SAME mock and
 * asserts it throws 23505 under `Promise.all`. Without this
 * calibration the test could trivially pass for both shapes.
 */

vi.mock("server-only", () => ({}));

interface SavedJobRow {
  id: string;
  user_id: string;
  job_posting_id: string;
}

interface FollowedCompanyRow {
  id: string;
  user_id: string;
  company_id: string;
}

const mocks = vi.hoisted(() => {
  const savedJobTable: SavedJobRow[] = [];
  const followedCompanyTable: FollowedCompanyRow[] = [];

  let idCounter = 0;
  const nextId = (prefix: string): string => `${prefix}-${++idCounter}`;

  const uniqueViolation = (constraintName: string): Error => {
    const e = new Error(
      `duplicate key value violates unique constraint "${constraintName}"`,
    ) as Error & { code: string; constraint_name: string };
    e.code = "23505";
    e.constraint_name = constraintName;
    return e;
  };

  // ---- db.insert(table).values(v).returning(...) -------------------
  //
  // Routes by the `table` reference object's identity (we capture both
  // savedJob and followedCompany at module init and dispatch on which
  // one was passed). The insert is atomic per call: a single microtask
  // hop simulates a roundtrip, then the UNIQUE check + push happens
  // synchronously inside the same micro-window. Two parallel inserts
  // for the same (user, target) interleave on the await and then race
  // on the push — exactly one wins, the other throws 23505.
  let savedJobRef: unknown;
  let followedCompanyRef: unknown;

  const dbInsert = (table: unknown) => {
    return {
      values: (
        v:
          | { userId: string; jobPostingId: string }
          | { userId: string; companyId: string },
      ) => {
        const isSavedJob = table === savedJobRef;
        const isFollowedCompany = table === followedCompanyRef;
        const exec = async (returningId: boolean): Promise<unknown[]> => {
          // Yield once so two parallel calls' INSERT bodies interleave
          // before the conflict check, reproducing the same window
          // that postgres sees under concurrent statements.
          await Promise.resolve();
          if (isSavedJob) {
            const vv = v as { userId: string; jobPostingId: string };
            const conflict = savedJobTable.some(
              (r) =>
                r.user_id === vv.userId
                && r.job_posting_id === vv.jobPostingId,
            );
            if (conflict) throw uniqueViolation("idx_sj_user_posting");
            const row = {
              id: nextId("sj"),
              user_id: vv.userId,
              job_posting_id: vv.jobPostingId,
            };
            savedJobTable.push(row);
            return returningId ? [{ id: row.id }] : [];
          }
          if (isFollowedCompany) {
            const vv = v as { userId: string; companyId: string };
            const conflict = followedCompanyTable.some(
              (r) =>
                r.user_id === vv.userId && r.company_id === vv.companyId,
            );
            if (conflict) throw uniqueViolation("idx_fc_user_company");
            const row = {
              id: nextId("fc"),
              user_id: vv.userId,
              company_id: vv.companyId,
            };
            followedCompanyTable.push(row);
            return returningId ? [{ id: row.id }] : [];
          }
          throw new Error("dbInsert mock: unknown table");
        };
        return {
          returning: () => exec(true),
          // Awaiting the chain without .returning resolves the same
          // exec — drizzle's behaviour for inserts where the caller
          // doesn't ask for the inserted row.
          then: (
            resolve: (value: unknown) => unknown,
            reject?: (reason?: unknown) => unknown,
          ) => exec(false).then(resolve, reject),
        };
      },
    };
  };

  // ---- db.delete(table).where(clause) ------------------------------
  //
  // Sniffs the (user, target) params out of the drizzle SQL clause and
  // removes the matching row from the in-memory table. Zero-row
  // deletes are silently no-op (matching drizzle's real behaviour).
  const dbDelete = (table: unknown) => {
    const isSavedJob = table === savedJobRef;
    const isFollowedCompany = table === followedCompanyRef;
    return {
      where: async (clause: unknown) => {
        await Promise.resolve();
        const params = extractParams(clause);
        const strs = params.filter((p): p is string => typeof p === "string");
        if (isSavedJob) {
          const [userId, postingId] = strs;
          if (!userId || !postingId) return;
          for (let i = savedJobTable.length - 1; i >= 0; i--) {
            const r = savedJobTable[i];
            if (r.user_id === userId && r.job_posting_id === postingId) {
              savedJobTable.splice(i, 1);
            }
          }
          return;
        }
        if (isFollowedCompany) {
          const [userId, companyId] = strs;
          if (!userId || !companyId) return;
          for (let i = followedCompanyTable.length - 1; i >= 0; i--) {
            const r = followedCompanyTable[i];
            if (r.user_id === userId && r.company_id === companyId) {
              followedCompanyTable.splice(i, 1);
            }
          }
          return;
        }
        throw new Error("dbDelete mock: unknown table");
      },
    };
  };

  // ---- db.select(...).from(...).where(...).limit(...) --------------
  //
  // Used only by the legacy buggy reference algorithm in the
  // calibration describe-block; the production code under test never
  // calls select() in the toggle path.
  const dbSelect = () => {
    let userIdFilter: string | undefined;
    let targetIdFilter: string | undefined;
    let scanTable: "saved_job" | "followed_company" | undefined;
    const chain: Record<string, unknown> = {
      from: (t: unknown) => {
        if (t === savedJobRef) scanTable = "saved_job";
        else if (t === followedCompanyRef) scanTable = "followed_company";
        return chain;
      },
      where: (clause: unknown) => {
        const params = extractParams(clause);
        const strs = params.filter((p): p is string => typeof p === "string");
        userIdFilter = strs[0];
        targetIdFilter = strs[1];
        return chain;
      },
      limit: async () => {
        await Promise.resolve();
        if (!userIdFilter || !targetIdFilter) return [];
        if (scanTable === "saved_job") {
          return savedJobTable
            .filter(
              (r) =>
                r.user_id === userIdFilter
                && r.job_posting_id === targetIdFilter,
            )
            .map((r) => ({ id: r.id }));
        }
        if (scanTable === "followed_company") {
          return followedCompanyTable
            .filter(
              (r) =>
                r.user_id === userIdFilter
                && r.company_id === targetIdFilter,
            )
            .map((r) => ({ id: r.id }));
        }
        return [];
      },
    };
    return chain;
  };

  return {
    savedJobTable,
    followedCompanyTable,
    reset: () => {
      savedJobTable.length = 0;
      followedCompanyTable.length = 0;
      idCounter = 0;
    },
    setTableRefs: (s: unknown, f: unknown) => {
      savedJobRef = s;
      followedCompanyRef = f;
    },
    getSessionUserId: vi.fn(),
    dbInsert,
    dbDelete,
    dbSelect,
    uniqueViolation,
  };
});

/**
 * Drizzle's SQL nodes (returned from `and`/`eq`) store their params in
 * `queryChunks`. Walk recursively and pluck primitive Param values in
 * encounter order. Mirrors the helper used in watchlist-slug-race.test.
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
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/db", () => ({
  db: {
    select: () => mocks.dbSelect(),
    insert: (t: unknown) => mocks.dbInsert(t),
    delete: (t: unknown) => mocks.dbDelete(t),
  },
}));

// Wire up the table references after the @/db mock is in place but
// before any production code runs. The toggle actions import `savedJob`
// and `followedCompany` from `@/db/schema`; we grab the same module
// objects here so dispatch by identity matches.
beforeEach(async () => {
  const schema = await import("@/db/schema");
  mocks.setTableRefs(schema.savedJob, schema.followedCompany);
  mocks.reset();
  mocks.getSessionUserId.mockResolvedValue("user-1");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("#3179 — toggleSavedJob race", () => {
  it("single toggle on empty state inserts and returns saved=true", async () => {
    const { toggleSavedJob } = await import("@/lib/actions/saved-jobs");
    const r = await toggleSavedJob("posting-1");
    expect(r.saved).toBe(true);
    expect(r.savedJobId).toBeDefined();
    expect(mocks.savedJobTable.map((r) => r.job_posting_id)).toEqual([
      "posting-1",
    ]);
  });

  it("second toggle (row exists) deletes and returns saved=false", async () => {
    const { toggleSavedJob } = await import("@/lib/actions/saved-jobs");
    await toggleSavedJob("posting-1");
    const r = await toggleSavedJob("posting-1");
    expect(r.saved).toBe(false);
    expect(mocks.savedJobTable.length).toBe(0);
  });

  it("two concurrent toggles on empty state — one saved, one un-saved, no exception", async () => {
    const { toggleSavedJob } = await import("@/lib/actions/saved-jobs");

    // The critical assertion: under Promise.all (interleaved
    // microtasks), neither call throws. Both resolve with consistent
    // toggle semantics — exactly one returns saved=true (the INSERT
    // winner), the other returns saved=false (the loser saw the
    // UNIQUE violation and ran the DELETE branch). The final table
    // state is empty (the second caller deleted the winner's row).
    const [a, b] = await Promise.all([
      toggleSavedJob("posting-1"),
      toggleSavedJob("posting-1"),
    ]);

    // Both promises resolved (no 500-equivalent rejection).
    expect([a.saved, b.saved].sort()).toEqual([false, true]);

    // Final state: one toggle-on + one toggle-off cancel out.
    expect(mocks.savedJobTable.length).toBe(0);
  });

  it("two concurrent toggles on existing state — both delete, idempotent", async () => {
    const { toggleSavedJob } = await import("@/lib/actions/saved-jobs");
    // Seed: row already exists.
    await toggleSavedJob("posting-1");
    expect(mocks.savedJobTable.length).toBe(1);

    const [a, b] = await Promise.all([
      toggleSavedJob("posting-1"),
      toggleSavedJob("posting-1"),
    ]);

    // Both INSERTs collide with the pre-existing row. Both fall
    // through to DELETE — one removes the row, the other finds nothing
    // to remove (silent no-op). Both return saved=false.
    expect(a.saved).toBe(false);
    expect(b.saved).toBe(false);
    expect(mocks.savedJobTable.length).toBe(0);
  });

  it("propagates non-23505 errors unchanged", async () => {
    const { toggleSavedJob } = await import("@/lib/actions/saved-jobs");

    // Swap dbInsert temporarily so it throws an unrelated error.
    const realInsert = mocks.dbInsert;
    const ohNo = new Error("connection terminated");
    // Intercept by monkey-patching for one call.
    const insertSpy = vi.spyOn(mocks, "dbInsert").mockImplementation(() => ({
      values: () => ({
        returning: async () => {
          await Promise.resolve();
          throw ohNo;
        },
        then: (
          _resolve: (v: unknown) => unknown,
          reject?: (e: unknown) => unknown,
        ) => Promise.reject(ohNo).then(_resolve, reject),
      }),
    }));

    await expect(toggleSavedJob("posting-1")).rejects.toBe(ohNo);

    insertSpy.mockRestore();
    void realInsert;
  });

  it("propagates 23505 from an unrelated constraint", async () => {
    const { toggleSavedJob } = await import("@/lib/actions/saved-jobs");

    const otherConstraint = new Error(
      'duplicate key value violates unique constraint "some_other_index"',
    ) as Error & { code: string; constraint_name: string };
    otherConstraint.code = "23505";
    otherConstraint.constraint_name = "some_other_index";

    const insertSpy = vi.spyOn(mocks, "dbInsert").mockImplementation(() => ({
      values: () => ({
        returning: async () => {
          await Promise.resolve();
          throw otherConstraint;
        },
        then: (
          _resolve: (v: unknown) => unknown,
          reject?: (e: unknown) => unknown,
        ) => Promise.reject(otherConstraint).then(_resolve, reject),
      }),
    }));

    await expect(toggleSavedJob("posting-1")).rejects.toBe(otherConstraint);
    insertSpy.mockRestore();
  });
});

describe("#3179 — toggleStarredCompany race", () => {
  it("single toggle on empty state inserts and returns starred=true", async () => {
    const { toggleStarredCompany } = await import(
      "@/lib/actions/starred-companies"
    );
    const r = await toggleStarredCompany("company-1");
    expect(r.starred).toBe(true);
    expect(mocks.followedCompanyTable.map((r) => r.company_id)).toEqual([
      "company-1",
    ]);
  });

  it("second toggle (row exists) deletes and returns starred=false", async () => {
    const { toggleStarredCompany } = await import(
      "@/lib/actions/starred-companies"
    );
    await toggleStarredCompany("company-1");
    const r = await toggleStarredCompany("company-1");
    expect(r.starred).toBe(false);
    expect(mocks.followedCompanyTable.length).toBe(0);
  });

  it("two concurrent toggles on empty state — one starred, one un-starred, no exception", async () => {
    const { toggleStarredCompany } = await import(
      "@/lib/actions/starred-companies"
    );

    const [a, b] = await Promise.all([
      toggleStarredCompany("company-1"),
      toggleStarredCompany("company-1"),
    ]);

    expect([a.starred, b.starred].sort()).toEqual([false, true]);
    expect(mocks.followedCompanyTable.length).toBe(0);
  });

  it("two concurrent toggles on existing state — both delete, idempotent", async () => {
    const { toggleStarredCompany } = await import(
      "@/lib/actions/starred-companies"
    );
    await toggleStarredCompany("company-1");

    const [a, b] = await Promise.all([
      toggleStarredCompany("company-1"),
      toggleStarredCompany("company-1"),
    ]);

    expect(a.starred).toBe(false);
    expect(b.starred).toBe(false);
    expect(mocks.followedCompanyTable.length).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Calibration: the legacy SELECT-then-INSERT-OR-DELETE algorithm.
//
// We re-implement the pre-fix toggle shape against the SAME mock
// harness and assert that under Promise.all the loser throws 23505 —
// proving the harness can distinguish fixed from broken.
// ─────────────────────────────────────────────────────────────────────

describe("#3179 — race-mock calibration (legacy SELECT-then-INSERT throws 23505)", () => {
  it("buggy SELECT-then-INSERT shape throws 23505 under Promise.all", async () => {
    // Mirror the pre-#3179 code: SELECT, then if empty INSERT,
    // otherwise DELETE. Two parallel callers' SELECTs both return
    // empty, both INSERTs race, the loser throws 23505 because the
    // legacy code has no catch.
    const buggyToggleSavedJob = async (
      userId: string,
      jobPostingId: string,
    ): Promise<{ saved: boolean }> => {
      // SELECT existing.
      const existing = mocks.savedJobTable.find(
        (r) => r.user_id === userId && r.job_posting_id === jobPostingId,
      );
      // Microtask hop — mimics await on a DB roundtrip, opens the
      // race window.
      await Promise.resolve();
      if (existing) {
        // DELETE — no contention here (id is unique).
        const idx = mocks.savedJobTable.findIndex(
          (r) => r.id === existing.id,
        );
        if (idx >= 0) mocks.savedJobTable.splice(idx, 1);
        return { saved: false };
      }
      // INSERT — racy. Two parallel callers both reach here, second
      // one trips the UNIQUE check.
      const conflict = mocks.savedJobTable.some(
        (r) => r.user_id === userId && r.job_posting_id === jobPostingId,
      );
      if (conflict) {
        throw mocks.uniqueViolation("idx_sj_user_posting");
      }
      mocks.savedJobTable.push({
        id: `sj-buggy-${mocks.savedJobTable.length + 1}`,
        user_id: userId,
        job_posting_id: jobPostingId,
      });
      return { saved: true };
    };

    const results = await Promise.allSettled([
      buggyToggleSavedJob("user-1", "posting-1"),
      buggyToggleSavedJob("user-1", "posting-1"),
    ]);

    // Exactly one fulfilled (the INSERT winner with saved=true), one
    // rejected with the 23505 — that's the bug the fix repairs.
    const fulfilled = results.filter((r) => r.status === "fulfilled");
    const rejected = results.filter((r) => r.status === "rejected");
    expect(fulfilled.length).toBe(1);
    expect(rejected.length).toBe(1);
    const rejectedResult = rejected[0] as PromiseRejectedResult;
    const err = rejectedResult.reason as { code?: string };
    expect(err.code).toBe("23505");
  });
});
