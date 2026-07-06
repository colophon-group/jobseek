/**
 * Perf regression test (#3172).
 *
 * Asserts that `getMyJobs` batches the per-row `interviewCount` lookup
 * into ONE `GROUP BY` query instead of embedding a correlated scalar
 * subquery that postgres expands into N count(*) executions per page.
 *
 * Pre-fix shape — single `db.select(...).from(savedJob)…` with the
 * projection:
 *   interviewCount: sql<number>`(
 *     SELECT count(*)::int FROM application_interview ai
 *     WHERE ai.saved_job_id = ${savedJob.id}
 *   )`
 * Postgres plans this as N scalar subquery executions per returned row
 * (up to 20 per `/my-jobs` page). Warm cache hides it (<1 ms each via
 * `idx_ai_saved_job_round`), but cold pool burns ~50–150 ms.
 *
 * Post-fix shape:
 *   1. Outer SELECT joins savedJob + jobPosting + company, projecting
 *      everything EXCEPT interviewCount. Up to `limit` rows.
 *   2. Single `SELECT saved_job_id, count(*) … WHERE saved_job_id =
 *      ANY($1) GROUP BY saved_job_id` over the page's ids. Postgres
 *      uses the same `idx_ai_saved_job_round` index for a hash
 *      aggregate in one round trip.
 *   3. JS Map merge: ids absent from the GROUP BY result default to 0.
 *
 * Trade-off: one extra round-trip vs. the legacy single-statement plan
 * with embedded subqueries. The second statement is one hash aggregate
 * (cheap and constant in plan cost) instead of N scalar count(*) plans
 * postgres has to re-prepare per row.
 *
 * The tests pin:
 *   - call count (regression guard): exactly two `db.select(...)` plus
 *     the count() query — the inline subquery is gone.
 *   - correctness: counts merge into the right rows.
 *   - zero-interview case: rows without any interview row still come
 *     back with `interviewCount: 0` (the Map.get fallback).
 *   - empty-page short-circuit: zero saved jobs means no count query.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => {
  // Distinct sentinel object identities so the harness can tell which
  // schema entry was passed to `db.select(...).from(table)`. The
  // production module imports `applicationInterview` from `@/db/schema`
  // and calls `db.select(...).from(applicationInterview)`; we capture
  // that identity here and assert against it below.
  const savedJobTable = { __t: "savedJob" };
  const jobPostingTable = { __t: "jobPosting" };
  const companyTable = { __t: "company" };
  const applicationInterviewTable = {
    __t: "applicationInterview",
    savedJobId: { __t: "applicationInterview.savedJobId" },
  };
  return {
    getSessionUserId: vi.fn(),
    // Queue of select-result rows in declaration order. Each entry is
    // one `db.select(...).from(...)…` chain. The chain captures which
    // table was hit and resolves with `rows`.
    selectQueue: [] as Array<{ rows: unknown[] }>,
    // Captured calls (projection keys + which table was queried).
    selectCalls: [] as Array<{
      projectionKeys: string[];
      fromTable?: unknown;
    }>,
    // Sentinel identities — exported so test bodies can compare
    // `fromTable === mocks.applicationInterviewTable`.
    savedJobTable,
    jobPostingTable,
    companyTable,
    applicationInterviewTable,
  };
});

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/db/schema", () => ({
  savedJob: {
    id: { __c: "savedJob.id" },
    userId: { __c: "savedJob.userId" },
    status: { __c: "savedJob.status" },
    statusChangedAt: { __c: "savedJob.statusChangedAt" },
    savedAt: { __c: "savedJob.savedAt" },
    jobPostingId: { __c: "savedJob.jobPostingId" },
    appliedAt: { __c: "savedJob.appliedAt" },
    salaryMinOverride: { __c: "savedJob.salaryMinOverride" },
    salaryMaxOverride: { __c: "savedJob.salaryMaxOverride" },
    salaryCurrencyOverride: { __c: "savedJob.salaryCurrencyOverride" },
    salaryPeriodOverride: { __c: "savedJob.salaryPeriodOverride" },
    __table: mocks.savedJobTable,
  },
  jobPosting: {
    id: { __c: "jobPosting.id" },
    titles: { __c: "jobPosting.titles" },
    sourceUrl: { __c: "jobPosting.sourceUrl" },
    firstSeenAt: { __c: "jobPosting.firstSeenAt" },
    isActive: { __c: "jobPosting.isActive" },
    companyId: { __c: "jobPosting.companyId" },
    salaryMin: { __c: "jobPosting.salaryMin" },
    salaryMax: { __c: "jobPosting.salaryMax" },
    salaryCurrency: { __c: "jobPosting.salaryCurrency" },
    salaryPeriod: { __c: "jobPosting.salaryPeriod" },
    __table: mocks.jobPostingTable,
  },
  company: {
    id: { __c: "company.id" },
    name: { __c: "company.name" },
    slug: { __c: "company.slug" },
    icon: { __c: "company.icon" },
    __table: mocks.companyTable,
  },
  applicationInterview: {
    savedJobId: mocks.applicationInterviewTable.savedJobId,
    id: { __c: "applicationInterview.id" },
    round: { __c: "applicationInterview.round" },
    type: { __c: "applicationInterview.type" },
    scheduledAt: { __c: "applicationInterview.scheduledAt" },
    createdAt: { __c: "applicationInterview.createdAt" },
    __table: mocks.applicationInterviewTable,
  },
}));

// Drizzle helpers — return inert sentinels so the action's WHERE/JOIN
// construction doesn't crash. The harness only cares about what
// `db.select(...).from(table)` consumed.
vi.mock("drizzle-orm", () => {
  const sqlFn = (..._args: unknown[]) => ({ _isSql: true });
  sqlFn.join = (..._args: unknown[]) => ({ _isSqlJoin: true });
  return {
    eq: (..._args: unknown[]) => ({ _isEq: true }),
    and: (..._args: unknown[]) => ({ _isAnd: true }),
    desc: (..._args: unknown[]) => ({ _isDesc: true }),
    asc: (..._args: unknown[]) => ({ _isAsc: true }),
    count: (..._args: unknown[]) => ({ _isCount: true }),
    inArray: (..._args: unknown[]) => ({ _isInArray: true, args: _args }),
    sql: sqlFn,
  };
});

function dequeueRows(): unknown[] {
  const next = mocks.selectQueue.shift();
  if (!next) {
    throw new Error("[my-jobs-interview-count-batch test] select queue empty");
  }
  return next.rows;
}

function makeSelectChain(projectionKeys: string[]): Record<string, unknown> {
  const chain: Record<string, unknown> = {};
  let fromTable: unknown;

  const recordCall = () =>
    mocks.selectCalls.push({ projectionKeys, fromTable });

  chain.from = (t: unknown) => {
    fromTable = t;
    return chain;
  };
  chain.innerJoin = () => chain;
  chain.leftJoin = () => chain;
  chain.where = () => chain;
  chain.orderBy = () => chain;
  chain.groupBy = () => chain;
  chain.offset = () => chain;
  chain.limit = () => chain;
  // Awaitable terminator: returns the next queued rows AND records the
  // call. The action awaits the chain directly (no `.execute()`), so
  // `.then` is the seam.
  chain.then = (
    resolve: (v: unknown) => unknown,
    reject?: (err: unknown) => unknown,
  ) => {
    try {
      recordCall();
      const rows = dequeueRows();
      return Promise.resolve(rows).then(resolve, reject);
    } catch (err) {
      return Promise.reject(err).then(resolve, reject);
    }
  };
  return chain;
}

vi.mock("@/db", () => ({
  db: {
    select: (projection: Record<string, unknown>) => {
      const projectionKeys = Object.keys(projection ?? {});
      return makeSelectChain(projectionKeys);
    },
  },
}));

// Module under test — must come AFTER the vi.mock factories.
import { getMyJobs } from "../my-jobs";

const USER_ID = "user-1";

// Helper: build a fake "outer" row from the joined SELECT.
function fakeOuterRow(opts: { id: string; companyName?: string }) {
  const now = new Date("2026-05-14T00:00:00Z");
  return {
    id: opts.id,
    savedAt: now,
    status: "saved",
    statusChangedAt: now,
    appliedAt: null,
    salaryMinOverride: null,
    salaryMaxOverride: null,
    salaryCurrencyOverride: null,
    salaryPeriodOverride: null,
    postingId: `posting-${opts.id}`,
    postingTitle: `Title ${opts.id}`,
    postingSourceUrl: `https://example.com/${opts.id}`,
    postingFirstSeenAt: now,
    postingIsActive: true,
    postingSalaryMin: null,
    postingSalaryMax: null,
    postingSalaryCurrency: null,
    postingSalaryPeriod: null,
    companyId: `co-${opts.id}`,
    companyName: opts.companyName ?? `Company ${opts.id}`,
    companySlug: `slug-${opts.id}`,
    companyIcon: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.selectQueue = [];
  mocks.selectCalls = [];
  mocks.getSessionUserId.mockResolvedValue(USER_ID);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("#3172 — getMyJobs batches interviewCount via GROUP BY", () => {
  it("issues exactly TWO db.select queries: count + page; then a single GROUP BY for interview counts (not N correlated subqueries)", async () => {
    const pageRows = [
      fakeOuterRow({ id: "sj-1" }),
      fakeOuterRow({ id: "sj-2" }),
      fakeOuterRow({ id: "sj-3" }),
      fakeOuterRow({ id: "sj-4" }),
      fakeOuterRow({ id: "sj-5" }),
    ];

    // 1) total count, 2) outer page, 3) interview count GROUP BY
    mocks.selectQueue.push({ rows: [{ count: 5 }] });
    mocks.selectQueue.push({ rows: pageRows });
    mocks.selectQueue.push({
      rows: [
        { savedJobId: "sj-1", count: 0 },
        { savedJobId: "sj-2", count: 1 },
        { savedJobId: "sj-3", count: 2 },
        { savedJobId: "sj-4", count: 3 },
      ],
    });

    const { jobs, total } = await getMyJobs({ offset: 0, limit: 20 });

    expect(total).toBe(5);
    expect(jobs).toHaveLength(5);

    // EXACTLY three `db.select(...)` chains landed: the total-count,
    // the outer page, and the per-page GROUP BY. The legacy shape had
    // only TWO (count + page), with the interview count folded into the
    // page projection as a correlated subquery — but it expanded into
    // N count(*) plan executions inside postgres. This assertion pins
    // both the migration (third query exists) and the regression guard
    // (no MORE than three queries, no per-row fan-out via app code).
    expect(mocks.selectCalls).toHaveLength(3);

    // The third call's projection must reference the savedJobId and
    // count — those are the only fields the action's Map merge consumes.
    const countCall = mocks.selectCalls[2];
    expect(countCall.projectionKeys).toEqual(
      expect.arrayContaining(["savedJobId", "count"]),
    );
    // And it must be against the application_interview table (the
    // `__t` discriminator on our mock schema object pins which schema
    // entry was passed to `.from(...)`).
    expect(
      (countCall.fromTable as { __table?: { __t?: string } })?.__table?.__t,
    ).toBe("applicationInterview");
  });

  it("merges interview counts onto the right rows (0..3 per job)", async () => {
    const pageRows = [
      fakeOuterRow({ id: "sj-1" }),
      fakeOuterRow({ id: "sj-2" }),
      fakeOuterRow({ id: "sj-3" }),
      fakeOuterRow({ id: "sj-4" }),
      fakeOuterRow({ id: "sj-5" }),
    ];

    mocks.selectQueue.push({ rows: [{ count: 5 }] });
    mocks.selectQueue.push({ rows: pageRows });
    // sj-1: 0 interviews (absent from GROUP BY result — must default to 0)
    // sj-2: 1
    // sj-3: 2
    // sj-4: 3
    // sj-5: 0 (also absent — fallback path)
    mocks.selectQueue.push({
      rows: [
        { savedJobId: "sj-2", count: 1 },
        { savedJobId: "sj-3", count: 2 },
        { savedJobId: "sj-4", count: 3 },
      ],
    });

    const { jobs } = await getMyJobs({ offset: 0, limit: 20 });

    expect(jobs.map((j) => ({ id: j.id, ic: j.interviewCount }))).toEqual([
      { id: "sj-1", ic: 0 },
      { id: "sj-2", ic: 1 },
      { id: "sj-3", ic: 2 },
      { id: "sj-4", ic: 3 },
      { id: "sj-5", ic: 0 },
    ]);
  });

  it("returns jobs with count=0 when the page has zero interviews total", async () => {
    const pageRows = [
      fakeOuterRow({ id: "sj-1" }),
      fakeOuterRow({ id: "sj-2" }),
    ];

    mocks.selectQueue.push({ rows: [{ count: 2 }] });
    mocks.selectQueue.push({ rows: pageRows });
    // Empty GROUP BY result — postgres returns 0 rows when no
    // application_interview row matches any saved_job_id in the page.
    mocks.selectQueue.push({ rows: [] });

    const { jobs, total } = await getMyJobs({ offset: 0, limit: 20 });

    // The page still surfaces (regression: callers must not lose rows
    // just because the second query was empty). interviewCount = 0
    // for both via the Map.get(id) ?? 0 fallback.
    expect(total).toBe(2);
    expect(jobs).toHaveLength(2);
    expect(jobs.every((j) => j.interviewCount === 0)).toBe(true);
  });

  it("does not run the GROUP BY query when the user has zero saved jobs (short-circuit)", async () => {
    // Outer count returns 0 — action short-circuits before fetching
    // the page OR the interview counts. No second/third query fires.
    mocks.selectQueue.push({ rows: [{ count: 0 }] });

    const { jobs, total } = await getMyJobs({ offset: 0, limit: 20 });

    expect(total).toBe(0);
    expect(jobs).toEqual([]);
    // Only ONE db.select call: the count. No outer page, no GROUP BY.
    expect(mocks.selectCalls).toHaveLength(1);
  });

  it("does not pass an empty array to inArray when the page is empty after total>0 (regression guard)", async () => {
    // Synthetic edge case: total returns > 0 but the page slice is
    // empty (e.g. offset past the end). The action must NOT issue
    // `WHERE saved_job_id = ANY('{}')` — that's a tautologically-empty
    // filter postgres still has to plan, and some drivers reject it.
    mocks.selectQueue.push({ rows: [{ count: 100 }] });
    mocks.selectQueue.push({ rows: [] });
    // No third entry queued: if the action does fire the GROUP BY,
    // the queue-empty assertion in `dequeueRows` will surface as a
    // promise rejection and fail the test.

    const { jobs, total } = await getMyJobs({ offset: 9999, limit: 20 });

    expect(total).toBe(100);
    expect(jobs).toEqual([]);
    // Two queries: count + page. NO third interview-count query.
    expect(mocks.selectCalls).toHaveLength(2);
  });
});
