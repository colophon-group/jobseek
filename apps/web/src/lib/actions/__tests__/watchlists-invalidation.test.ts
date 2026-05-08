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
 * in apps/web/src/lib/actions/__tests__/company.test.ts.)
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
  tsUpsertWatchlist: vi.fn(),
  tsDeleteWatchlist: vi.fn(),
  tsUpdateWatchlistField: vi.fn(),
  generateUniqueSlug: vi.fn(),
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
}));

vi.mock("@/lib/indexnow", () => ({ notifyIndexNow: mocks.notifyIndexNow }));

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
  resolveLocationSlugs: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn().mockResolvedValue([]),
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
 * Read the source file at test time and statically determine which
 * exported `async function` definitions invoke `_invalidateWatchlistCaches`
 * within their body. This intentionally trades robustness against future
 * refactors (e.g. extracting the helper into another module) for catching
 * the regression class the issue is about: a refactor silently dropping
 * `await _invalidateWatchlistCaches(...)` from one of the 7 mutators —
 * or adding a new mutator that forgets to call it.
 *
 * If a new mutator is added without invalidation, the inferred set
 * differs from EXPECTED_MUTATORS and the test fails with a diff that
 * names the missing function. If a mutator is renamed or the helper is
 * inlined, the registry must be updated deliberately.
 */
const SOURCE_PATH = join(__dirname, "..", "watchlists.ts");

const EXPECTED_MUTATORS = new Set<string>([
  "createWatchlist",
  "updateWatchlist",
  "deleteWatchlist",
  "copyWatchlist",
  "addCompanyToWatchlist",
  "clearWatchlistCompanies",
  "removeCompanyFromWatchlist",
]);

/**
 * Crudely parse the file: find every `export async function <name>(`
 * and slice from there to the next `export async function` (or EOF).
 * For each slice, check whether `_invalidateWatchlistCaches(` appears.
 */
function extractMutatorsCallingInvalidator(source: string): Set<string> {
  const exportRe = /export\s+async\s+function\s+(\w+)\s*\(/g;
  const matches: { name: string; start: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = exportRe.exec(source)) !== null) {
    matches.push({ name: m[1], start: m.index });
  }
  const calling = new Set<string>();
  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].start;
    const end = i + 1 < matches.length ? matches[i + 1].start : source.length;
    const body = source.slice(start, end);
    if (body.includes("_invalidateWatchlistCaches(")) {
      calling.add(matches[i].name);
    }
  }
  return calling;
}

describe("invalidation registry guard", () => {
  it("every mutator that invalidates caches is in the expected registry (and vice versa)", () => {
    const source = readFileSync(SOURCE_PATH, "utf-8");
    const inferred = extractMutatorsCallingInvalidator(source);

    // Sorted arrays produce a readable diff if a mutator is added/removed.
    expect([...inferred].sort()).toEqual([...EXPECTED_MUTATORS].sort());
  });

  it("toggleWatchlistAlerts is NOT in the invalidating set (private-only mutation)", () => {
    const source = readFileSync(SOURCE_PATH, "utf-8");
    const inferred = extractMutatorsCallingInvalidator(source);
    expect(inferred.has("toggleWatchlistAlerts")).toBe(false);
  });

  it("registry size is exactly the count of mutators × 1 invalidation call site each", () => {
    // Cross-check: counting raw `_invalidateWatchlistCaches(` invocations
    // (excluding the helper definition itself) must equal the registry
    // size. If a mutator gains a second call site (e.g. extra slug
    // variant) update this assertion, but a forgotten call site fails.
    const source = readFileSync(SOURCE_PATH, "utf-8");
    // Strip the helper *definition* line so we only count call sites.
    const withoutDef = source.replace(
      /async\s+function\s+_invalidateWatchlistCaches\s*\(/,
      "",
    );
    const callSites = (withoutDef.match(/_invalidateWatchlistCaches\s*\(/g) || []).length;
    expect(callSites).toBe(EXPECTED_MUTATORS.size);
  });
});
