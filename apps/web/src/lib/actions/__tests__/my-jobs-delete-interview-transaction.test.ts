import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3075 — `deleteInterview` used to delete the target row, select all
 * remaining interviews, then issue one UPDATE per row to compact
 * `round`.
 *
 * That shape is both slower (N sequential writes) and non-atomic: a
 * failed mid-loop update could leave gaps, and the "last interview
 * deleted" status downgrade happened after the renumber work.
 *
 * The fixed action does all writes in one `db.transaction`, compacts
 * with a single row_number() CTE, and only updates saved_job.status
 * after the same transaction has proven no interviews remain.
 */

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => {
  const savedJob = {
    __t: "savedJob",
    id: { __c: "savedJob.id" },
    userId: { __c: "savedJob.userId" },
    status: { __c: "savedJob.status" },
    statusChangedAt: { __c: "savedJob.statusChangedAt" },
  };
  const applicationInterview = {
    __t: "applicationInterview",
    id: { __c: "applicationInterview.id" },
    savedJobId: { __c: "applicationInterview.savedJobId" },
    round: { __c: "applicationInterview.round" },
    createdAt: { __c: "applicationInterview.createdAt" },
  };

  type OwnerRow = {
    savedJobId: string;
    sjUserId: string;
    sjStatus: string;
  };

  let ownerRows: OwnerRow[] = [];
  let remainingCount = 0;

  const calls = {
    transactions: 0,
    deletes: [] as unknown[],
    executes: [] as string[],
    updates: [] as Array<{ table: unknown; values: Record<string, unknown> }>,
  };

  const getSessionUserId = vi.fn();

  const reset = () => {
    ownerRows = [];
    remainingCount = 0;
    calls.transactions = 0;
    calls.deletes = [];
    calls.executes = [];
    calls.updates = [];
    getSessionUserId.mockReset();
  };

  const select = () => {
    const chain = {
      from: () => chain,
      innerJoin: () => chain,
      where: () => chain,
      limit: async () => ownerRows,
    };
    return chain;
  };

  const deleteFrom = (table: unknown) => {
    const chain = {
      where: async () => {
        calls.deletes.push(table);
      },
    };
    return chain;
  };

  const update = (table: unknown) => {
    let values: Record<string, unknown> = {};
    const chain = {
      set: (nextValues: Record<string, unknown>) => {
        values = nextValues;
        return chain;
      },
      where: async () => {
        calls.updates.push({ table, values });
      },
    };
    return chain;
  };

  const execute = async (sqlObj: unknown) => {
    const text = (sqlObj as { text?: string }).text ?? "";
    calls.executes.push(text);
    if (/remaining_count/i.test(text)) {
      return [{ remaining_count: remainingCount }];
    }
    return [];
  };

  const tx = {
    select,
    delete: deleteFrom,
    update,
    execute,
  };

  return {
    savedJob,
    applicationInterview,
    calls,
    getSessionUserId,
    reset,
    setOwnerRow: (row: OwnerRow | null) => {
      ownerRows = row ? [row] : [];
    },
    setRemainingCount: (count: number) => {
      remainingCount = count;
    },
    db: {
      transaction: async <T>(fn: (transaction: typeof tx) => Promise<T>) => {
        calls.transactions += 1;
        return fn(tx);
      },
    },
  };
});

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/db/schema", () => ({
  savedJob: mocks.savedJob,
  applicationInterview: mocks.applicationInterview,
  jobPosting: {},
  company: {},
}));

vi.mock("@/db", () => ({
  db: mocks.db,
}));

vi.mock("drizzle-orm", () => {
  const sqlFn = (
    strings: TemplateStringsArray,
    ...values: unknown[]
  ): { text: string; values: unknown[] } => ({
    text: Array.from(strings).join("?"),
    values,
  });
  sqlFn.join = (..._args: unknown[]) => ({ text: "join", values: [] });

  return {
    eq: (..._args: unknown[]) => ({ _isEq: true }),
    and: (..._args: unknown[]) => ({ _isAnd: true }),
    asc: (..._args: unknown[]) => ({ _isAsc: true }),
    desc: (..._args: unknown[]) => ({ _isDesc: true }),
    count: (..._args: unknown[]) => ({ _isCount: true }),
    inArray: (..._args: unknown[]) => ({ _isInArray: true }),
    sql: sqlFn,
  };
});

import { deleteInterview } from "../my-jobs";

beforeEach(() => {
  mocks.reset();
  mocks.getSessionUserId.mockResolvedValue("user-1");
});

describe("#3075 — deleteInterview renumbers atomically", () => {
  it("uses one transaction and a row_number CTE instead of per-row interview updates", async () => {
    mocks.setOwnerRow({
      savedJobId: "00000000-0000-0000-0000-000000000001",
      sjUserId: "user-1",
      sjStatus: "interviewing",
    });
    mocks.setRemainingCount(2);

    const result = await deleteInterview(
      "00000000-0000-0000-0000-000000000099",
    );

    expect(result).toEqual({ ok: true });
    expect(mocks.calls.transactions).toBe(1);
    expect(mocks.calls.deletes).toEqual([mocks.applicationInterview]);

    const renumberSql = mocks.calls.executes.find((text) =>
      /row_number\(\)/i.test(text),
    );
    expect(renumberSql).toMatch(/WITH ordered AS/i);
    expect(renumberSql).toMatch(/UPDATE application_interview AS ai/i);
    expect(renumberSql).toMatch(/remaining_count/i);

    expect(
      mocks.calls.updates.filter(
        (call) => call.table === mocks.applicationInterview,
      ),
    ).toHaveLength(0);
    expect(
      mocks.calls.updates.filter((call) => call.table === mocks.savedJob),
    ).toHaveLength(0);
  });

  it("downgrades an interviewing saved job when the deleted row was the last interview", async () => {
    mocks.setOwnerRow({
      savedJobId: "00000000-0000-0000-0000-000000000001",
      sjUserId: "user-1",
      sjStatus: "interviewing",
    });
    mocks.setRemainingCount(0);

    const result = await deleteInterview(
      "00000000-0000-0000-0000-000000000099",
    );

    expect(result).toEqual({ ok: true });
    expect(mocks.calls.transactions).toBe(1);
    expect(mocks.calls.updates).toHaveLength(1);
    expect(mocks.calls.updates[0].table).toBe(mocks.savedJob);
    expect(mocks.calls.updates[0].values).toMatchObject({
      status: "applied",
    });
    expect(mocks.calls.updates[0].values.statusChangedAt).toBeInstanceOf(Date);
  });

  it("returns not_found without mutating when the interview is absent or owned by another user", async () => {
    mocks.setOwnerRow({
      savedJobId: "00000000-0000-0000-0000-000000000001",
      sjUserId: "user-2",
      sjStatus: "interviewing",
    });

    const result = await deleteInterview(
      "00000000-0000-0000-0000-000000000099",
    );

    expect(result).toEqual({ ok: false, error: "not_found" });
    expect(mocks.calls.transactions).toBe(1);
    expect(mocks.calls.deletes).toHaveLength(0);
    expect(mocks.calls.executes).toHaveLength(0);
    expect(mocks.calls.updates).toHaveLength(0);
  });
});
