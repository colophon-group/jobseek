import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Server actions transitively import `server-only`, which throws in a
// non-Next runtime. Neutralize before module-under-test loads.
vi.mock("server-only", () => ({}));

/**
 * Shared mocks must live in `vi.hoisted` because `vi.mock` hoists to the
 * top of the file; closure references against module-scope variables
 * become `undefined` at the time the factory runs. (Same pattern used
 * in apps/web/src/lib/services/__tests__/company-detail.test.ts.)
 */
const mocks = vi.hoisted(() => ({
  // Drizzle fluent-API surface used by mutators. Each call returns the
  // chain object until the leaf — `limit` (selects), `returning` (insert),
  // or `where` for delete/update — which resolves with whatever the test
  // queues via the corresponding *Result mock.
  selectLimitResult: vi.fn(),
  insertReturningResult: vi.fn(),

  // Raw SQL — used by `_getOwnerInfo` (and `_countWatchlistCompanies`,
  // `_getWatchlistMirrorCount`).
  dbExecute: vi.fn(),

  getSessionUserId: vi.fn(),

  updateTag: vi.fn(),
  invalidate: vi.fn(),
  invalidatePattern: vi.fn(),
  cached: vi.fn(),

  // `after` is fire-and-forget in production — the action calls it but
  // does NOT `await` the registered callback. The Next runtime drains
  // the queue after the response is flushed. Capture every callback
  // here and let each test `await flushAfterQueue()` before asserting,
  // so the post-mutation invalidation work is observable.
  afterCallbacks: [] as (() => Promise<void> | void)[],
  afterFn: vi.fn((cb: () => Promise<void> | void) => {
    mocks.afterCallbacks.push(cb);
  }),

  notifyIndexNow: vi.fn().mockResolvedValue({ kind: "submitted", status: 200, urlCount: 0 }),
  logIndexNowResult: vi.fn(),
  tsUpsertWatchlist: vi.fn(),
  tsDeleteWatchlist: vi.fn(),
  tsUpdateWatchlistField: vi.fn(),
  generateUniqueSlug: vi.fn(),
  // #3201: the new helper wraps `generateUniqueSlug` + the INSERT with
  // a retry-on-23505 loop. For unrelated cache-invalidation tests we
  // mock it as a thin pass-through: pick a slug via the same
  // `generateUniqueSlug` stub and run the inserter exactly once.
  insertWatchlistWithUniqueSlug: vi.fn(
    async (
      userId: string,
      title: string,
      insert: (slug: string) => Promise<unknown>,
    ) => {
      const slug = await mocks.generateUniqueSlug(userId, title);
      const row = await insert(slug);
      return { row, slug };
    },
  ),
  canCreateWatchlist: vi.fn().mockResolvedValue({ allowed: true }),
  getUserPlan: vi.fn().mockResolvedValue("free"),
}));

// ---- vi.mock() factory blocks (hoisted by vitest) ------------------

vi.mock("next/cache", () => ({ updateTag: mocks.updateTag }));
vi.mock("next/server", () => ({ after: mocks.afterFn }));

vi.mock("@/lib/cache", () => ({
  invalidate: mocks.invalidate,
  invalidatePattern: mocks.invalidatePattern,
  cached: mocks.cached,
}));

// `cache-tags` is the unit under test for tag-name correctness; let the
// real implementation flow so we assert against the canonical strings
// (`watchlist:<userSlug>:<watchlistSlug>`).

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/lib/viewer", () => ({
  getViewerLanguages: vi.fn().mockResolvedValue(["en"]),
}));

vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: mocks.canCreateWatchlist,
  getUserPlan: mocks.getUserPlan,
  PLAN_LIMITS: { free: { canReceiveAlerts: false }, paid: { canReceiveAlerts: true } },
}));

vi.mock("@/lib/watchlist-slug", () => ({
  generateUniqueSlug: mocks.generateUniqueSlug,
  insertWatchlistWithUniqueSlug: mocks.insertWatchlistWithUniqueSlug,
}));

vi.mock("@/lib/indexnow", () => ({
  notifyIndexNow: mocks.notifyIndexNow,
  logIndexNowResult: mocks.logIndexNowResult,
}));

vi.mock("@/lib/search/typesense-watchlist", () => ({
  upsertWatchlist: mocks.tsUpsertWatchlist,
  deleteWatchlist: mocks.tsDeleteWatchlist,
  updateWatchlistField: mocks.tsUpdateWatchlistField,
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: vi.fn() }) }),
  }),
}));

vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: vi.fn(),
}));

vi.mock("@/lib/search/pg-filters", () => ({
  localesOrNoneClause: vi.fn(),
}));

vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_WATCHLIST_POSTINGS: 50,
  COMPANY_BATCH_SIZE: 100,
}));

vi.mock("@/lib/actions/locations", () => ({
  expandLocationIds: vi.fn().mockResolvedValue([]),
  expandLocationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveLocationSlugs: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn().mockResolvedValue([]),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveOccupationSlugs: vi.fn().mockResolvedValue([]),
  resolveSenioritySlugs: vi.fn().mockResolvedValue([]),
  resolveTechnologySlugs: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/lib/services/taxonomy", () => ({
  expandOccupationIds: vi.fn().mockResolvedValue([]),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
  resolveOccupationSlugs: vi.fn().mockResolvedValue([]),
  resolveSenioritySlugs: vi.fn().mockResolvedValue([]),
  resolveTechnologySlugs: vi.fn().mockResolvedValue([]),
}));

vi.mock("drizzle-orm", () => ({
  sql: (..._args: unknown[]) => ({ _isSql: true }),
  eq: (..._args: unknown[]) => ({ _isEq: true }),
  and: (..._args: unknown[]) => ({ _isAnd: true }),
}));

// `@/db/schema` is referenced as `watchlist`, `watchlistCompany`, `company`
// table objects — the queries don't introspect the columns once the
// drizzle chain is mocked, so opaque sentinels are sufficient.
vi.mock("@/db/schema", () => ({
  watchlist: {
    id: { _col: "id" },
    userId: { _col: "userId" },
    slug: { _col: "slug" },
    title: { _col: "title" },
    description: { _col: "description" },
    isPublic: { _col: "isPublic" },
    alertsEnabled: { _col: "alertsEnabled" },
    filters: { _col: "filters" },
  },
  watchlistCompany: {
    watchlistId: { _col: "watchlistId" },
    companyId: { _col: "companyId" },
  },
  company: {},
}));

// Drizzle fluent chain mock. `select()` returns an object whose
// `.from(...).where(...).limit(N)` resolves with the queued result;
// likewise `insert` -> values -> returning, `update`/`delete` ->
// chained `.set`/`.where` => resolve. This mirrors how the production
// code consumes the API without coupling to drizzle internals.
function makeChain(leafResultFn: () => Promise<unknown>) {
  const chain: Record<string, unknown> = {};
  // Methods that just return the chain to keep fluency:
  for (const m of ["from", "where", "set", "values", "onConflictDoNothing"]) {
    chain[m] = () => chain;
  }
  // Leaf-ish methods that resolve the chain when awaited.
  chain.limit = () => Promise.resolve(leafResultFn());
  chain.returning = () => Promise.resolve(leafResultFn());
  // Some chains end at `.where(...)` (no `.limit`) — the chain itself
  // must therefore be thenable for `await db.delete(...).where(...)`.
  chain.then = (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) =>
    Promise.resolve(leafResultFn()).then(resolve, reject);
  return chain;
}

vi.mock("@/db", () => ({
  db: {
    select: () => makeChain(() => Promise.resolve(mocks.selectLimitResult())),
    insert: () => makeChain(() => Promise.resolve(mocks.insertReturningResult())),
    update: () => makeChain(() => Promise.resolve(undefined)),
    delete: () => makeChain(() => Promise.resolve(undefined)),
    execute: (...args: unknown[]) => mocks.dbExecute(...args),
  },
}));

// ---- Module under test (must come AFTER vi.mock blocks) ------------

import {
  createWatchlist,
  updateWatchlist,
  deleteWatchlist,
  copyWatchlist,
  addCompanyToWatchlist,
  clearWatchlistCompanies,
  removeCompanyFromWatchlist,
  toggleWatchlistAlerts,
} from "../watchlists";

// ---- Test helpers ---------------------------------------------------

const USER_ID = "user-1";
const USER_NAME = "username-1";
const DISPLAY_USER_NAME = "DisplayUser1";
const SLUG = "my-watchlist";
const NEW_SLUG = "renamed-watchlist";
const WATCHLIST_ID = "wl-1";

function expectedTagPair(slug: string) {
  return [
    `watchlist:${USER_NAME}:${slug}`,
    `watchlist:${DISPLAY_USER_NAME}:${slug}`,
  ];
}

function expectedInvalidateKeyPair(slug: string) {
  return [
    `public-watchlist:${USER_NAME}:${slug}`,
    `public-watchlist:${DISPLAY_USER_NAME}:${slug}`,
  ];
}

/**
 * Queue the `_getOwnerInfo` SQL response — `db.execute` is called with the
 * SELECT username/display_username query inside `_invalidateWatchlistCaches`.
 * Returning a row with both fields populated lets us assert that BOTH
 * the `username` and `displayUsername` slug variants get invalidated.
 */
function queueOwnerInfo() {
  mocks.dbExecute.mockResolvedValue([
    { name: "Alice", username: USER_NAME, display_username: DISPLAY_USER_NAME },
  ]);
}

/**
 * Drain every callback registered via `after(...)` during the action
 * call. Production runs them after-response; vitest needs them to
 * resolve before the assertion phase, so each test awaits this between
 * the action call and the spy assertions.
 *
 * Awaits sequentially because the action under test registers multiple
 * `after()` callbacks and the order matters for `dbExecute` queue
 * consumption (e.g. `_getOwnerInfo` then `_countWatchlistCompanies`).
 */
async function flushAfterQueue(): Promise<void> {
  while (mocks.afterCallbacks.length > 0) {
    const cb = mocks.afterCallbacks.shift()!;
    await cb();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  // Re-prime the always-on stubs that vi.clearAllMocks() reset.
  mocks.canCreateWatchlist.mockResolvedValue({ allowed: true });
  mocks.getUserPlan.mockResolvedValue("free");
  mocks.notifyIndexNow.mockResolvedValue({ kind: "submitted", status: 200, urlCount: 0 });
  mocks.afterFn.mockImplementation((cb: () => Promise<void> | void) => {
    mocks.afterCallbacks.push(cb);
  });
  mocks.getSessionUserId.mockResolvedValue(USER_ID);
  mocks.generateUniqueSlug.mockResolvedValue(SLUG);
  // Re-prime the helper pass-through that vi.clearAllMocks() reset.
  mocks.insertWatchlistWithUniqueSlug.mockImplementation(
    async (
      userId: string,
      title: string,
      insert: (slug: string) => Promise<unknown>,
    ) => {
      const slug = await mocks.generateUniqueSlug(userId, title);
      const row = await insert(slug);
      return { row, slug };
    },
  );
  mocks.afterCallbacks.length = 0;
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- Per-mutator happy-path tests -----------------------------------

describe("watchlist mutator cache invalidation", () => {
  it("createWatchlist invalidates both username + displayUsername slug variants", async () => {
    queueOwnerInfo();
    mocks.insertReturningResult.mockResolvedValue([{ id: WATCHLIST_ID }]);

    await createWatchlist({
      title: "My Watchlist",
      companyIds: ["co-1", "co-2"],
      isPublic: true,
    });
    await flushAfterQueue();

    const tagCalls = mocks.updateTag.mock.calls.map((c) => c[0]);
    expect(tagCalls.sort()).toEqual(expectedTagPair(SLUG).sort());
    const invalidateCalls = mocks.invalidate.mock.calls.map((c) => c[0]);
    expect(invalidateCalls.sort()).toEqual(expectedInvalidateKeyPair(SLUG).sort());
  });

  it("createWatchlist upserts indexed filters_json for public anyCompany watchlists", async () => {
    queueOwnerInfo();
    mocks.insertReturningResult.mockResolvedValue([{ id: WATCHLIST_ID }]);
    const { resolveLocationSlugs } = await import("@/lib/actions/locations");
    const { resolveOccupationSlugs } = await import("@/lib/services/taxonomy");
    vi.mocked(resolveLocationSlugs).mockResolvedValueOnce(
      new Map([
        ["switzerland", { id: 30, slug: "switzerland", name: "Switzerland", type: "country", parentName: null }],
      ]),
    );
    vi.mocked(resolveOccupationSlugs).mockResolvedValueOnce(
      new Map([
        ["account-executive", { id: 101, slug: "account-executive", name: "Account Executive" }],
      ]),
    );

    await createWatchlist({
      title: "Enterprise Sales in Switzerland",
      companyIds: [],
      filters: {
        anyCompany: true,
        locationSlugs: ["switzerland"],
        occupationSlugs: ["account-executive"],
      },
      isPublic: true,
    });
    await flushAfterQueue();

    expect(mocks.tsUpsertWatchlist).toHaveBeenCalledTimes(1);
    const doc = mocks.tsUpsertWatchlist.mock.calls[0][0];
    const payload = JSON.parse(doc.filters_json);
    expect(payload).toMatchObject({
      anyCompany: true,
      locationSlugs: ["switzerland"],
      locationIds: [30],
      occupationSlugs: ["account-executive"],
      occupationIds: [101],
    });
  });

  it("updateWatchlist invalidates current slug pair when title is unchanged", async () => {
    queueOwnerInfo();
    mocks.selectLimitResult.mockResolvedValue([
      {
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: SLUG,
        title: "Existing",
        description: null,
        isPublic: true,
        filters: {},
      },
    ]);
    // _countWatchlistCompanies → cnt: 0 (so trivial → no Typesense)
    mocks.dbExecute
      .mockResolvedValueOnce([{ name: "Alice", username: USER_NAME, display_username: DISPLAY_USER_NAME }])
      .mockResolvedValueOnce([{ cnt: 0 }]);

    await updateWatchlist({
      watchlistId: WATCHLIST_ID,
      description: "New description",
    });
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
  });

  it("deleteWatchlist invalidates both slug variants", async () => {
    queueOwnerInfo();
    mocks.selectLimitResult.mockResolvedValue([
      { userId: USER_ID, slug: SLUG, isPublic: true },
    ]);

    await deleteWatchlist(WATCHLIST_ID);
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
  });

  it("copyWatchlist invalidates new copy's slug for both variants", async () => {
    queueOwnerInfo();
    // First select: source watchlist row; subsequent selects: copied
    // companies (returns []).
    mocks.selectLimitResult
      .mockResolvedValueOnce([
        { title: "Source", description: null, filters: {}, isPublic: true, userId: "other" },
      ]);
    // `db.select(... companyId).from(watchlistCompany).where(eq(...))`
    // resolves via the chain too (no `.limit`); make the chain
    // resolve on `.where` for the companies query.
    // makeChain.then resolves with selectLimitResult() — when no more
    // queued, returns `undefined`. The companies query needs `[]`:
    mocks.selectLimitResult.mockResolvedValueOnce([]);
    // Mirror count
    mocks.dbExecute
      .mockResolvedValueOnce([{ name: "Alice", username: USER_NAME, display_username: DISPLAY_USER_NAME }])
      .mockResolvedValueOnce([{ cnt: 0 }]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "wl-copy" }]);

    await copyWatchlist(WATCHLIST_ID);
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
  });

  it("addCompanyToWatchlist invalidates both slug variants for public watchlists", async () => {
    queueOwnerInfo();
    mocks.selectLimitResult.mockResolvedValue([
      { userId: USER_ID, slug: SLUG, isPublic: true },
    ]);
    mocks.dbExecute
      .mockResolvedValueOnce([{ name: "Alice", username: USER_NAME, display_username: DISPLAY_USER_NAME }])
      .mockResolvedValueOnce([{ cnt: 1 }]);

    await addCompanyToWatchlist(WATCHLIST_ID, "co-9");
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
  });

  it("clearWatchlistCompanies invalidates both slug variants for public watchlists", async () => {
    queueOwnerInfo();
    mocks.selectLimitResult.mockResolvedValue([
      { userId: USER_ID, slug: SLUG, isPublic: true },
    ]);

    await clearWatchlistCompanies(WATCHLIST_ID);
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
  });

  it("removeCompanyFromWatchlist invalidates both slug variants for public watchlists", async () => {
    queueOwnerInfo();
    mocks.selectLimitResult.mockResolvedValue([
      { userId: USER_ID, slug: SLUG, isPublic: true },
    ]);
    mocks.dbExecute
      .mockResolvedValueOnce([{ name: "Alice", username: USER_NAME, display_username: DISPLAY_USER_NAME }])
      .mockResolvedValueOnce([{ cnt: 0 }]);

    await removeCompanyFromWatchlist(WATCHLIST_ID, "co-9");
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
  });
});

// ---- Special-case behavior ------------------------------------------

describe("updateWatchlist title rename", () => {
  it("invalidates BOTH old and new slug for both username variants (4 tag + 4 invalidate calls)", async () => {
    queueOwnerInfo();
    mocks.selectLimitResult.mockResolvedValue([
      {
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: SLUG, // old slug
        title: "Old title",
        description: null,
        isPublic: true,
        filters: {},
      },
    ]);
    mocks.generateUniqueSlug.mockResolvedValue(NEW_SLUG); // new slug differs
    mocks.dbExecute
      .mockResolvedValueOnce([{ name: "Alice", username: USER_NAME, display_username: DISPLAY_USER_NAME }])
      .mockResolvedValueOnce([{ cnt: 0 }]);

    await updateWatchlist({
      watchlistId: WATCHLIST_ID,
      title: "New title",
    });
    await flushAfterQueue();

    // 2 slug variants × 2 username variants = 4 calls each.
    const tagCalls = mocks.updateTag.mock.calls.map((c) => c[0]);
    expect(tagCalls).toHaveLength(4);
    expect(new Set(tagCalls)).toEqual(
      new Set([...expectedTagPair(SLUG), ...expectedTagPair(NEW_SLUG)]),
    );

    const invalidateCalls = mocks.invalidate.mock.calls.map((c) => c[0]);
    expect(invalidateCalls).toHaveLength(4);
    expect(new Set(invalidateCalls)).toEqual(
      new Set([
        ...expectedInvalidateKeyPair(SLUG),
        ...expectedInvalidateKeyPair(NEW_SLUG),
      ]),
    );
  });
});

describe("createWatchlist trivial public watchlist", () => {
  /**
   * Round-5 fix (PR #2888): cache invalidation runs unconditionally for
   * public watchlists, even trivial ones, to bust any pre-existing
   * null-detail render cached at the page level. Typesense + IndexNow
   * are gated on !trivial; only invalidation must fire here.
   */
  it("still invalidates caches when no companies + no filters", async () => {
    queueOwnerInfo();
    mocks.insertReturningResult.mockResolvedValue([{ id: WATCHLIST_ID }]);

    await createWatchlist({
      title: "Empty",
      companyIds: [],
      isPublic: true,
      // no filters → trivial
    });
    await flushAfterQueue();

    expect(mocks.updateTag.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedTagPair(SLUG).sort(),
    );
    expect(mocks.invalidate.mock.calls.map((c) => c[0]).sort()).toEqual(
      expectedInvalidateKeyPair(SLUG).sort(),
    );
    // Trivial → must NOT touch Typesense / IndexNow.
    expect(mocks.tsUpsertWatchlist).not.toHaveBeenCalled();
    expect(mocks.notifyIndexNow).not.toHaveBeenCalled();
  });
});

describe("toggleWatchlistAlerts (private-only mutation)", () => {
  it("does NOT call updateTag or invalidate", async () => {
    mocks.selectLimitResult.mockResolvedValue([
      { userId: USER_ID, alertsEnabled: false },
    ]);
    mocks.getUserPlan.mockResolvedValue("paid");

    await toggleWatchlistAlerts(WATCHLIST_ID);
    await flushAfterQueue();

    expect(mocks.updateTag).not.toHaveBeenCalled();
    expect(mocks.invalidate).not.toHaveBeenCalled();
    // Defensively also assert no after() callbacks were even registered.
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });
});

// ---- Static guard test ----------------------------------------------

/**
 * Read the source file at test time and statically check that every
 * exported async function which mutates a watchlist row (or its
 * `watchlist_company` join rows) is categorized into one of two
 * registries:
 *
 *   EXPECTED_INVALIDATING_MUTATORS — calls `_invalidateWatchlistCaches`.
 *   EXPECTED_NON_INVALIDATING       — knowingly does not, with a 1-line
 *                                     reason (private-only fields, etc).
 *
 * The previous shape of this guard collected only the names of functions
 * that already called the helper and asserted the inferred set equalled
 * EXPECTED_MUTATORS. That was unidirectional: a new exported async
 * mutator that *forgot* to call the helper never landed in the inferred
 * set, so the comparison still held and the regression slipped through.
 *
 * The new shape walks ALL exported async functions, classifies each by
 * (mutates? invalidates?), and fails with a named diagnostic for the
 * offending function whenever a mutator drifts from its declared bucket.
 */
const SOURCE_PATH = join(__dirname, "..", "..", "services", "watchlists.ts");

/** Mutators that MUST call `_invalidateWatchlistCaches`. */
const EXPECTED_INVALIDATING_MUTATORS = new Set<string>([
  "createWatchlist",
  "updateWatchlist",
  "deleteWatchlist",
  "copyWatchlist",
  "addCompanyToWatchlist",
  "clearWatchlistCompanies",
  "removeCompanyFromWatchlist",
]);

/**
 * Mutators that touch watchlist (or watchlist_company) rows but do NOT
 * need to invalidate the public watchlist caches. Each entry must have
 * a one-line comment justifying why no invalidation is required.
 */
const EXPECTED_NON_INVALIDATING = new Set<string>([
  // alertsEnabled is a private field — never rendered on the public page,
  // so no public cache key is affected by toggling it.
  "toggleWatchlistAlerts",
  // lastAccessedAt is a private "fire-and-forget" analytics touch on the
  // owner's read path; it never appears in any public render.
  "getWatchlistByUserAndSlug",
]);

/**
 * Enumerate every exported async function in the source file.
 *
 * Regex: /^[ \t]*export\s+(?:default\s+)?async\s+function\s+(\w+)\s*\(/gm
 *   - matches `export async function name(` and `export default async function name(`
 *   - the leading [ \t]* + the `m` flag confine matches to start-of-line so
 *     a comment containing the literal phrase can't be picked up
 *
 * Cases this regex misses (documented honest limitations):
 *   - `export const name = async () => {...}` and `export const name = async function() {...}`
 *     → not currently used in watchlists.ts; if introduced, this guard would
 *     silently skip them. Add a parallel arrow-form regex if/when needed.
 *   - `export { name }` re-exports of an internally-defined async function
 *     → also not used here.
 *   - JSDoc / multi-line-string occurrences of the pattern. The leading-
 *     whitespace anchor avoids the comment block case in practice.
 */
function enumerateExportedAsyncFunctions(
  source: string,
): { name: string; start: number }[] {
  const exportRe = /^[ \t]*export\s+(?:default\s+)?async\s+function\s+(\w+)\s*\(/gm;
  const matches: { name: string; start: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = exportRe.exec(source)) !== null) {
    matches.push({ name: m[1], start: m.index });
  }
  return matches;
}

/**
 * Returns the slice of `source` corresponding to the i-th function
 * body. We can't reliably brace-balance without a real parser, so we
 * approximate the body as everything up to the next `export ...`
 * boundary (or the next non-exported top-level `async function` /
 * `function`, to avoid pulling private helpers into the previous
 * function's slice). For our checks (substring matches against
 * mutation patterns + `_invalidateWatchlistCaches(`) the
 * approximation is sound: any token introduced inside the next
 * function still sits past the export anchor we slice on.
 */
function functionBody(
  source: string,
  matches: { name: string; start: number }[],
  i: number,
): string {
  const start = matches[i].start;
  // End at either the next exported async function OR the next
  // top-level (column-0 or whitespace-prefixed) `async function _xxx`
  // private helper definition. These are anchored similarly so we don't
  // bleed into the next sibling.
  const tail = source.slice(start);
  const nextExport = tail.search(/\n[ \t]*export\s+(?:default\s+)?async\s+function\s+\w+\s*\(/);
  // Also stop at private helpers `async function _xxx(` so the LAST
  // export's body doesn't include the helper definitions at the bottom
  // of the file (which would let `_invalidateWatchlistCaches` definition
  // leak into the slice).
  const nextPrivate = tail.search(/\nasync\s+function\s+_\w+\s*\(/);
  const candidates = [nextExport, nextPrivate].filter((n) => n >= 0);
  const offset = candidates.length > 0 ? Math.min(...candidates) : tail.length;
  return tail.slice(0, offset);
}

/**
 * Detect whether the function body mutates a watchlist row (or one of its
 * `watchlist_company` join rows). We accept both inline (`db.update(watchlist)`)
 * and chained (`db\n  .update(watchlist)`) forms.
 *
 * The trailing `\)` is essential: it pins the table identifier, so
 * `watchlist` doesn't false-match `watchlistCompany` as a substring.
 */
const MUTATION_RE = /\.(update|insert|delete)\(\s*(watchlist|watchlistCompany)\s*\)/;

function bodyMutatesWatchlist(body: string): boolean {
  return MUTATION_RE.test(body);
}

function bodyCallsInvalidator(body: string): boolean {
  return body.includes("_invalidateWatchlistCaches(");
}

type Classification =
  | "mutates_and_invalidates"
  | "mutates_no_invalidate"
  | "invalidates_no_mutation"
  | "neither";

function classify(body: string): Classification {
  const mutates = bodyMutatesWatchlist(body);
  const invalidates = bodyCallsInvalidator(body);
  if (mutates && invalidates) return "mutates_and_invalidates";
  if (mutates && !invalidates) return "mutates_no_invalidate";
  if (!mutates && invalidates) return "invalidates_no_mutation";
  return "neither";
}

describe("invalidation registry guard", () => {
  it("every exported async function is correctly bucketed by (mutates?, invalidates?)", () => {
    const source = readFileSync(SOURCE_PATH, "utf-8");
    const matches = enumerateExportedAsyncFunctions(source);

    const errors: string[] = [];

    for (let i = 0; i < matches.length; i++) {
      const { name } = matches[i];
      const body = functionBody(source, matches, i);
      const cls = classify(body);

      switch (cls) {
        case "mutates_and_invalidates": {
          if (!EXPECTED_INVALIDATING_MUTATORS.has(name)) {
            errors.push(
              `Function \`${name}\` mutates a watchlist AND calls _invalidateWatchlistCaches, but is not listed in EXPECTED_INVALIDATING_MUTATORS. Add it to that set in watchlists-invalidation.test.ts (or remove the invalidation if it isn't needed).`,
            );
          }
          break;
        }
        case "mutates_no_invalidate": {
          if (!EXPECTED_NON_INVALIDATING.has(name)) {
            errors.push(
              `Function \`${name}\` mutates a watchlist but doesn't call \`_invalidateWatchlistCaches\`. Either add the call, or add \`${name}\` to EXPECTED_NON_INVALIDATING with a comment explaining why.`,
            );
          }
          break;
        }
        case "invalidates_no_mutation": {
          // Unusual — invalidation without a paired mutation in the same
          // function. Could be a follow-up cron/cleanup; flag for review
          // rather than silently allow.
          errors.push(
            `Function \`${name}\` calls \`_invalidateWatchlistCaches\` without any visible watchlist mutation in its body. Confirm this is intentional (e.g. a cleanup/cron path) and either move the invalidation closer to the mutator or document why.`,
          );
          break;
        }
        case "neither": {
          // Read-only or helper. Just guard against stale entries in
          // either registry — a function that no longer mutates AND no
          // longer invalidates shouldn't be claimed by either list.
          if (EXPECTED_INVALIDATING_MUTATORS.has(name)) {
            errors.push(
              `Function \`${name}\` is listed in EXPECTED_INVALIDATING_MUTATORS but its body neither mutates a watchlist nor calls _invalidateWatchlistCaches. Remove it from the set or restore the missing logic.`,
            );
          }
          if (EXPECTED_NON_INVALIDATING.has(name)) {
            errors.push(
              `Function \`${name}\` is listed in EXPECTED_NON_INVALIDATING but its body no longer mutates a watchlist. Remove it from the set.`,
            );
          }
          break;
        }
      }
    }

    expect(errors).toEqual([]);
  });

  it("EXPECTED_INVALIDATING_MUTATORS members all exist as exported async functions", () => {
    const source = readFileSync(SOURCE_PATH, "utf-8");
    const exportedNames = new Set(
      enumerateExportedAsyncFunctions(source).map((m) => m.name),
    );
    for (const name of EXPECTED_INVALIDATING_MUTATORS) {
      expect(exportedNames.has(name), `${name} not found as exported async function`).toBe(true);
    }
  });

  it("EXPECTED_NON_INVALIDATING members all exist as exported async functions", () => {
    const source = readFileSync(SOURCE_PATH, "utf-8");
    const exportedNames = new Set(
      enumerateExportedAsyncFunctions(source).map((m) => m.name),
    );
    for (const name of EXPECTED_NON_INVALIDATING) {
      expect(exportedNames.has(name), `${name} not found as exported async function`).toBe(true);
    }
  });

  it("the two registries are disjoint", () => {
    const overlap = [...EXPECTED_INVALIDATING_MUTATORS].filter((n) =>
      EXPECTED_NON_INVALIDATING.has(n),
    );
    expect(overlap).toEqual([]);
  });

  it("toggleWatchlistAlerts is in EXPECTED_NON_INVALIDATING (private-only field)", () => {
    expect(EXPECTED_NON_INVALIDATING.has("toggleWatchlistAlerts")).toBe(true);
    expect(EXPECTED_INVALIDATING_MUTATORS.has("toggleWatchlistAlerts")).toBe(false);
  });

  it("invalidation call-site count equals EXPECTED_INVALIDATING_MUTATORS size", () => {
    // Cross-check: counting raw `_invalidateWatchlistCaches(` invocations
    // (excluding the helper definition itself) must equal the registry
    // size. If a mutator gains a second call site, this assertion would
    // need updating — but a forgotten call site still fails.
    const source = readFileSync(SOURCE_PATH, "utf-8");
    const withoutDef = source.replace(
      /async\s+function\s+_invalidateWatchlistCaches\s*\(/,
      "",
    );
    const callSites = (withoutDef.match(/_invalidateWatchlistCaches\s*\(/g) || []).length;
    expect(callSites).toBe(EXPECTED_INVALIDATING_MUTATORS.size);
  });
});
