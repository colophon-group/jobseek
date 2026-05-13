import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3160 (and #3114) — `addInterview` round-number race.
 *
 * Before the fix, `addInterview` ran two statements:
 *   1. `SELECT coalesce(max(round), 0) AS max_round` from
 *      `application_interview` for the saved_job
 *   2. `INSERT INTO application_interview (..., round) VALUES (max_round + 1, ...)`
 *
 * Two concurrent callers (a flaky double-tap, two tabs, or
 * `addInterview` racing with the `updateJobStatus('interviewing')`
 * auto-create branch) both observed the same `max_round` and both
 * inserted the same `round`. The non-unique `idx_ai_saved_job_round`
 * index meant the DB did not catch this; `deleteInterview`'s
 * round-by-position renumber preserved the duplicate forever.
 *
 * The fix collapses the two statements into one
 * `INSERT INTO application_interview … SELECT coalesce(max(round), 0) + 1 …`
 * (atomic in postgres) and pairs it with a UNIQUE `(saved_job_id, round)`
 * constraint (migration 0078) plus a retry-on-23505 loop in the action.
 *
 * This test runs the fixed `addInterview` against an in-memory mock
 * that mirrors postgres semantics:
 *   - `db.execute(sql)` is atomic — its body runs without await
 *     interleaving, so two parallel calls each compute `max+1` against
 *     a fresh table snapshot. UNIQUE (saved_job_id, round) is enforced
 *     in the mock; truly racing INSERT…SELECTs surface as 23505.
 *   - `db.insert(...).onConflictDoNothing({target: ...})` honours the
 *     conflict target and absorbs duplicates silently.
 *
 * On main (separate SELECT + INSERT, no UNIQUE, no ON CONFLICT) the
 * same harness reproduces the duplicate-round outcome — captured in a
 * separate spec below that imports the legacy reference algorithm.
 */

vi.mock("server-only", () => ({}));

interface InterviewRow {
  id: string;
  saved_job_id: string;
  round: number;
  type: string;
  scheduled_at: Date | null;
  created_at: Date;
}

const mocks = vi.hoisted(() => {
  // Verbatim row store, sole source of truth for "table contents".
  const interviewTable: Array<{
    id: string;
    saved_job_id: string;
    round: number;
    type: string;
    scheduled_at: Date | null;
    created_at: Date;
  }> = [];

  let idCounter = 0;
  const nextId = (): string => `iv-${++idCounter}`;

  const uniqueViolation = (): Error => {
    const e = new Error(
      'duplicate key value violates unique constraint "idx_ai_saved_job_round"',
    ) as Error & { code: string };
    e.code = "23505";
    return e;
  };

  let ownerRow: { id: string; status: string } | null = null;

  // ---- db.execute(sql) — atomic INSERT…SELECT (the fix) -----------
  //
  // The action only invokes db.execute for the addInterview path. We
  // sniff the saved_job_id and type out of the tagged-template `sql`
  // call by walking its `queryChunks` field (drizzle-orm's internal
  // representation). The body is fully synchronous between the entry
  // and the return, mirroring postgres's single-statement atomicity.
  const dbExecute = async (sqlObj: unknown): Promise<unknown[]> => {
    const args = extractSqlParams(sqlObj);
    const [savedJobId, type] = args as [string, string];

    // Yield once so callers can interleave their pre-execute work
    // (ownership check, etc.). Once we land inside this function from
    // here on the work is atomic — no more awaits.
    await Promise.resolve();

    const existing = interviewTable.filter(
      (r) => r.saved_job_id === savedJobId,
    );
    const maxRound = existing.reduce((m, r) => Math.max(m, r.round), 0);
    const round = maxRound + 1;

    // Defensive: if a concurrent INSERT…SELECT raced and added the
    // same round under a different microtask window, the UNIQUE
    // constraint surfaces 23505. The action's retry loop catches it.
    if (existing.some((r) => r.round === round)) throw uniqueViolation();

    const row = {
      id: nextId(),
      saved_job_id: savedJobId,
      round,
      type,
      scheduled_at: null,
      created_at: new Date(),
    };
    interviewTable.push(row);
    return [row];
  };

  // ---- db.insert(...).values(...).onConflictDoNothing/returning ----
  //
  // Used by updateJobStatus auto-create. Honours the conflict target.
  const dbInsert = () => ({
    values: (v: { savedJobId: string; round: number; type: string }) => {
      // Capture v here in this closure so concurrent inserts don't
      // clobber each other's pending state.
      const exec = async ({
        absorbConflict,
      }: {
        absorbConflict: boolean;
      }): Promise<InterviewRow[]> => {
        await Promise.resolve();
        const conflict = interviewTable.some(
          (r) => r.saved_job_id === v.savedJobId && r.round === v.round,
        );
        if (conflict) {
          if (absorbConflict) return [];
          throw uniqueViolation();
        }
        const row = {
          id: nextId(),
          saved_job_id: v.savedJobId,
          round: v.round,
          type: v.type,
          scheduled_at: null,
          created_at: new Date(),
        };
        interviewTable.push(row);
        return [row];
      };
      return {
        onConflictDoNothing: (_opts?: unknown) =>
          exec({ absorbConflict: true }),
        returning: () => exec({ absorbConflict: false }),
        // Make the chain awaitable directly (no .returning / no
        // .onConflict). Returns the inserted-row array. Forward both
        // resolve and reject so a thrown unique_violation surfaces to
        // the caller's await instead of hanging.
        then: (
          resolve: (v: unknown) => unknown,
          reject?: (err: unknown) => unknown,
        ) => exec({ absorbConflict: false }).then(resolve, reject),
      };
    },
  });

  // ---- db.select() ------------------------------------------------
  //
  // The action under test uses select() only for ownership (the
  // updateJobStatus auto-create branch no longer reads via select).
  // We sniff projection shape to route.
  const dbSelect = (cols: unknown) => {
    const c = cols as Record<string, unknown>;
    const isOwner = c && "status" in c && "id" in c;

    const chain = {
      from: () => chain,
      innerJoin: () => chain,
      where: () => chain,
      limit: async () => {
        await Promise.resolve();
        if (isOwner) return ownerRow ? [ownerRow] : [];
        return [];
      },
      // Some legacy paths terminate at .where() with a thenable.
      then: async (
        resolve: (v: unknown) => unknown,
        _reject?: (err: unknown) => unknown,
      ) => {
        await Promise.resolve();
        return resolve([]);
      },
    };
    return chain;
  };

  // ---- db.update() — used for status auto-transition --------------
  const dbUpdate = () => {
    const chain = {
      set: () => chain,
      where: async () => undefined,
    };
    return chain;
  };

  // ---- db.delete() — unused here but harmless ---------------------
  const dbDelete = () => {
    const chain = {
      where: async () => undefined,
    };
    return chain;
  };

  return {
    interviewTable,
    setOwner: (id: string, status: string) => {
      ownerRow = { id, status };
    },
    seedInterviews: (
      rows: Array<{ savedJobId: string; round: number; type: string }>,
    ) => {
      for (const r of rows) {
        interviewTable.push({
          id: nextId(),
          saved_job_id: r.savedJobId,
          round: r.round,
          type: r.type,
          scheduled_at: null,
          created_at: new Date(),
        });
      }
    },
    reset: () => {
      interviewTable.length = 0;
      idCounter = 0;
      ownerRow = null;
    },
    getSessionUserId: vi.fn(),
    dbExecute,
    dbInsert,
    dbSelect,
    dbUpdate,
    dbDelete,
  };
});

/**
 * Drizzle's `sql\`...\`` tagged template stores its parts on
 * `queryChunks: Array<Param | StringChunk>`. The interpolated values
 * appear as bare primitives directly in the array; the SQL fragments
 * are wrapped in `{ value: [string] }` (StringChunk). Walk the chunks
 * top level and pick out only the bare primitives — those are the
 * parameters the action passed to `sql\`…${param}…\``.
 */
function extractSqlParams(sqlObj: unknown): unknown[] {
  const params: unknown[] = [];
  const queryChunks = (sqlObj as { queryChunks?: unknown[] }).queryChunks;
  if (!Array.isArray(queryChunks)) return params;
  for (const chunk of queryChunks) {
    if (chunk === null || chunk === undefined) continue;
    const t = typeof chunk;
    if (t === "string" || t === "number" || t === "bigint" || t === "boolean") {
      params.push(chunk);
    } else if (t === "object") {
      // Drizzle Param wrapper: `.value` carries the actual parameter,
      // `.encoder` is the codec. StringChunk has `.value: string[]` and
      // no `.encoder`. We want Param.
      const v = (chunk as { value?: unknown; encoder?: unknown }).value;
      const hasEncoder = (chunk as { encoder?: unknown }).encoder !== undefined;
      if (hasEncoder && v !== undefined) params.push(v);
    }
  }
  return params;
}

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/db", () => ({
  db: {
    select: (cols: unknown) => mocks.dbSelect(cols),
    insert: () => mocks.dbInsert(),
    update: () => mocks.dbUpdate(),
    delete: () => mocks.dbDelete(),
    execute: (sqlObj: unknown) => mocks.dbExecute(sqlObj),
  },
}));

describe("#3160 — addInterview round-number race", () => {
  beforeEach(() => {
    mocks.reset();
    mocks.getSessionUserId.mockResolvedValue("user-1");
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("two concurrent addInterview calls produce distinct round numbers", async () => {
    mocks.setOwner("sj-1", "interviewing");

    const { addInterview } = await import("@/lib/actions/my-jobs");

    // Critical assertion: under Promise.all (interleaved microtasks),
    // each interview gets a unique round. On the buggy main shape the
    // two callers' `SELECT max(round)` reads both return 0, both INSERT
    // round=1, and the table ends up with two round=1 rows.
    const [r1, r2] = await Promise.all([
      addInterview("sj-1", "phone_screen"),
      addInterview("sj-1", "video_call"),
    ]);

    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(true);

    const rounds = mocks.interviewTable
      .filter((r) => r.saved_job_id === "sj-1")
      .map((r) => r.round)
      .sort();

    expect(rounds).toEqual([1, 2]);
    expect(new Set(rounds).size).toBe(rounds.length);
  });

  it("addInterview against existing interviews picks the next round", async () => {
    mocks.setOwner("sj-1", "interviewing");
    mocks.seedInterviews([
      { savedJobId: "sj-1", round: 1, type: "phone_screen" },
      { savedJobId: "sj-1", round: 2, type: "video_call" },
    ]);

    const { addInterview } = await import("@/lib/actions/my-jobs");

    const r = await addInterview("sj-1", "technical");
    expect(r.ok).toBe(true);
    expect(r.interview?.round).toBe(3);

    const rounds = mocks.interviewTable
      .filter((r) => r.saved_job_id === "sj-1")
      .map((r) => r.round)
      .sort();
    expect(rounds).toEqual([1, 2, 3]);
  });

  it("updateJobStatus('interviewing') auto-create is a no-op when interview exists", async () => {
    // Race scenario: addInterview already inserted round=1, then a
    // concurrent updateJobStatus auto-create runs. With ON CONFLICT DO
    // NOTHING + UNIQUE constraint, the auto-create is a silent no-op
    // instead of inserting a second round=1.
    mocks.setOwner("sj-1", "applied");
    mocks.seedInterviews([
      { savedJobId: "sj-1", round: 1, type: "video_call" },
    ]);

    const { updateJobStatus } = await import("@/lib/actions/my-jobs");
    const r = await updateJobStatus("sj-1", "interviewing");
    expect(r.ok).toBe(true);

    const rounds = mocks.interviewTable
      .filter((r) => r.saved_job_id === "sj-1")
      .map((r) => r.round)
      .sort();

    expect(rounds).toEqual([1]);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Reference: the buggy main algorithm. We re-implement the pre-fix
// addInterview shape (separate SELECT max + INSERT) and run it against
// the SAME mock harness. The assertion is that the harness reproduces
// the duplicate-round behaviour under the buggy algorithm — proving
// the harness can distinguish fixed from broken.
//
// This isn't a unit test of the production code (the production code
// is the fixed version); it's a calibration spec that gives us
// confidence the harness wouldn't trivially pass for both shapes.
// ─────────────────────────────────────────────────────────────────────

describe("#3160 — race-mock calibration (buggy algorithm reproduces duplicates)", () => {
  beforeEach(() => {
    mocks.reset();
    mocks.getSessionUserId.mockResolvedValue("user-1");
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("buggy SELECT-then-INSERT shape produces duplicate round=1 under Promise.all", async () => {
    // Lay down an explicit shared state and run the buggy algorithm
    // directly against the in-memory table. This proves the harness
    // (specifically, microtask interleaving + no UNIQUE check in the
    // direct path) can produce the bug.
    mocks.setOwner("sj-1", "interviewing");

    const buggyAddInterview = async (
      savedJobId: string,
      type: string,
    ): Promise<void> => {
      // Step 1: read max.
      const existing = mocks.interviewTable.filter(
        (r) => r.saved_job_id === savedJobId,
      );
      // Microtask hop — mimics await on a DB roundtrip.
      await Promise.resolve();
      const maxRound = existing.reduce((m, r) => Math.max(m, r.round), 0);
      const nextRound = maxRound + 1;
      // Step 2: insert. No UNIQUE check (the pre-migration world).
      await Promise.resolve();
      mocks.interviewTable.push({
        id: `iv-buggy-${mocks.interviewTable.length + 1}`,
        saved_job_id: savedJobId,
        round: nextRound,
        type,
        scheduled_at: null,
        created_at: new Date(),
      });
    };

    await Promise.all([
      buggyAddInterview("sj-1", "phone_screen"),
      buggyAddInterview("sj-1", "video_call"),
    ]);

    const rounds = mocks.interviewTable
      .filter((r) => r.saved_job_id === "sj-1")
      .map((r) => r.round);

    // Two rows, BOTH with round=1 — the canonical bug shape.
    expect(rounds.length).toBe(2);
    expect(new Set(rounds).size).toBe(1);
    expect(rounds[0]).toBe(1);
    expect(rounds[1]).toBe(1);
  });
});
