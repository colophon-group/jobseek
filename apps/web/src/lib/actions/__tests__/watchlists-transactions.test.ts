import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
// Materialization has its own identity and real-Postgres contract suites.
vi.mock("@/lib/services/company-references", () => ({
  prepareCompanyReferences: mocks.prepareCompanyReferences,
  persistCompanyReferences: mocks.persistCompanyReferences,
  persistExistingCompanyReferences: vi.fn().mockResolvedValue(undefined),
  companyReferenceErrorResult: (error: unknown) => {
    if (error && typeof error === "object" && "code" in error) return { error: error.code };
    throw error;
  },
}));


const mocks = vi.hoisted(() => {
  type WatchlistRow = {
    id: string;
    userId: string;
    slug: string;
    title: string;
    isPublic: boolean;
    shareEnabled?: boolean;
    filters: Record<string, unknown>;
    sourceWatchlistId?: string | null;
  };
  type CompanyRow = { watchlistId: string; companyId: string };
  type State = { watchlists: WatchlistRow[]; companies: CompanyRow[] };

  const watchlist = {
    __table: "watchlist",
    id: { __column: "watchlist.id" },
    userId: { __column: "watchlist.userId" },
    slug: { __column: "watchlist.slug" },
    title: { __column: "watchlist.title" },
    isPublic: { __column: "watchlist.isPublic" },
    shareEnabled: { __column: "watchlist.shareEnabled" },
    alertsEnabled: { __column: "watchlist.alertsEnabled" },
    filters: { __column: "watchlist.filters" },
  };
  const watchlistCompany = {
    __table: "watchlistCompany",
    watchlistId: { __column: "watchlistCompany.watchlistId" },
    companyId: { __column: "watchlistCompany.companyId" },
  };

  let committed: State = { watchlists: [], companies: [] };
  let rootSelectRows: unknown[][] = [];
  let failCompanyInsert = false;
  let slugConflictsRemaining = 0;
  let nextWatchlistId = "wl-new";

  const calls = {
    transactions: 0,
    rollbacks: 0,
  };
  const afterFn = vi.fn();
  const getSessionUserId = vi.fn();
  const canCreateWatchlist = vi.fn();
  const sharedCloneLimit = vi.fn();
  const prepareCompanyReferences = vi.fn();
  const persistCompanyReferences = vi.fn();

  const cloneState = (state: State): State => ({
    watchlists: state.watchlists.map((row) => ({ ...row })),
    companies: state.companies.map((row) => ({ ...row })),
  });

  const makeSelect = (
    state: State,
    useRootQueue: boolean,
    projection?: Record<string, unknown>,
  ) => {
    let table: unknown;
    const rows = () => {
      if (
        projection
        && "value" in projection
        && (projection.value as { kind?: string }).kind === "count"
      ) {
        return [{ value: state.watchlists.filter((row) => row.userId === "user-1").length }];
      }
      if (useRootQueue) return rootSelectRows.shift() ?? [];
      if (table === watchlistCompany) {
        return state.companies.map(({ companyId }) => ({ companyId }));
      }
      if (table === watchlist && projection) {
        return state.watchlists.map((row) => Object.fromEntries(
          Object.keys(projection).map((key) => [
            key,
            key === "isShared"
              ? Boolean(row.shareEnabled)
              : row[key as keyof WatchlistRow],
          ]),
        ));
      }
      return state.watchlists;
    };
    const chain: Record<string, unknown> = {};
    chain.from = (nextTable: unknown) => {
      table = nextTable;
      return chain;
    };
    chain.where = () => chain;
    chain.for = () => chain;
    chain.limit = async () => rows();
    chain.then = (
      resolve: (value: unknown) => unknown,
      reject?: (reason: unknown) => unknown,
    ) => Promise.resolve(rows()).then(resolve, reject);
    return chain;
  };

  const makeInsert = (state: State, table: unknown) => {
    let insertedValues: Record<string, unknown>[] = [];
    const chain: Record<string, unknown> = {};
    chain.values = (values: Record<string, unknown> | Record<string, unknown>[]) => {
      insertedValues = Array.isArray(values) ? values : [values];
      return chain;
    };
    chain.onConflictDoNothing = () => chain;

    const apply = () => {
      if (table === watchlistCompany) {
        if (failCompanyInsert) throw new Error("forced watchlist_company insert failure");
        state.companies.push(...insertedValues as CompanyRow[]);
        return [];
      }
      if (table === watchlist) {
        const value = insertedValues[0];
        if (slugConflictsRemaining > 0) {
          slugConflictsRemaining -= 1;
          throw Object.assign(new Error("duplicate watchlist slug"), {
            code: "23505",
            constraint_name: "idx_wl_user_slug",
          });
        }
        const row: WatchlistRow = {
          id: nextWatchlistId,
          userId: String(value.userId),
          slug: String(value.slug),
          title: String(value.title),
          isPublic: Boolean(value.isPublic),
          shareEnabled: Boolean(value.shareEnabled),
          filters: (value.filters as Record<string, unknown>) ?? {},
          sourceWatchlistId: (value.sourceWatchlistId as string | null) ?? null,
        };
        state.watchlists.push(row);
        return [{ id: row.id }];
      }
      throw new Error("unexpected insert table");
    };

    chain.returning = async () => apply();
    chain.then = (
      resolve: (value: unknown) => unknown,
      reject?: (reason: unknown) => unknown,
    ) => Promise.resolve().then(apply).then(resolve, reject);
    return chain;
  };

  const makeUpdate = (state: State, table: unknown) => {
    let updates: Record<string, unknown> = {};
    let applied = false;
    let result: Array<{ id: string }> = [];
    const chain: Record<string, unknown> = {};
    chain.set = (values: Record<string, unknown>) => {
      updates = values;
      return chain;
    };
    const apply = () => {
      if (applied) return result;
      applied = true;
      if (table !== watchlist) throw new Error("unexpected update table");
      const row = state.watchlists[0];
      const isShareTransition = updates.shareEnabled === true;
      if (
        row
        && (!isShareTransition || (row.userId === "user-1" && !row.shareEnabled))
      ) {
        Object.assign(row, updates);
        result = [{ id: row.id }];
      }
      return result;
    };
    chain.where = () => chain;
    chain.returning = async () => apply();
    chain.then = (
      resolve: (value: unknown) => unknown,
      reject?: (reason: unknown) => unknown,
    ) => Promise.resolve().then(apply).then(resolve, reject);
    return chain;
  };

  const makeDelete = (state: State, table: unknown) => {
    const chain: Record<string, unknown> = {};
    chain.where = async () => {
      if (table !== watchlistCompany) throw new Error("unexpected delete table");
      state.companies = [];
      return [];
    };
    return chain;
  };

  const makeTx = (state: State) => ({
    execute: async () => [],
    select: (projection?: Record<string, unknown>) => makeSelect(state, false, projection),
    insert: (table: unknown) => makeInsert(state, table),
    update: (table: unknown) => makeUpdate(state, table),
    delete: (table: unknown) => makeDelete(state, table),
  });

  const db = {
    select: (projection?: Record<string, unknown>) => makeSelect(committed, true, projection),
    insert: () => {
      throw new Error("mutation escaped transaction");
    },
    update: (table: unknown) => makeUpdate(committed, table),
    delete: () => {
      throw new Error("mutation escaped transaction");
    },
    transaction: async <T>(fn: (tx: ReturnType<typeof makeTx>) => Promise<T>) => {
      calls.transactions += 1;
      const draft = cloneState(committed);
      try {
        const result = await fn(makeTx(draft));
        committed = draft;
        return result;
      } catch (error) {
        calls.rollbacks += 1;
        throw error;
      }
    },
  };

  const reset = () => {
    committed = { watchlists: [], companies: [] };
    rootSelectRows = [];
    failCompanyInsert = false;
    slugConflictsRemaining = 0;
    nextWatchlistId = "wl-new";
    calls.transactions = 0;
    calls.rollbacks = 0;
    afterFn.mockReset();
    getSessionUserId.mockReset();
    canCreateWatchlist.mockReset();
    sharedCloneLimit.mockReset();
    prepareCompanyReferences.mockReset().mockResolvedValue([]);
    persistCompanyReferences.mockReset().mockResolvedValue(undefined);
  };

  return {
    watchlist,
    watchlistCompany,
    db,
    calls,
    afterFn,
    getSessionUserId,
    canCreateWatchlist,
    sharedCloneLimit,
    prepareCompanyReferences,
    persistCompanyReferences,
    reset,
    snapshot: () => cloneState(committed),
    setState: (state: State) => {
      committed = cloneState(state);
    },
    queueRootSelect: (...rows: unknown[][]) => {
      rootSelectRows.push(...rows);
    },
    failCompanyInserts: () => {
      failCompanyInsert = true;
    },
    failSlugInserts: (count: number) => {
      slugConflictsRemaining = count;
    },
    setNextWatchlistId: (id: string) => {
      nextWatchlistId = id;
    },
  };
});

vi.mock("next/server", () => ({ after: mocks.afterFn }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn(), updateTag: vi.fn() }));
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));
vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: mocks.canCreateWatchlist,
  getUserPlan: vi.fn(),
  PLAN_LIMITS: { free: {}, paid: {} },
}));
vi.mock("@/lib/rate-limit", () => ({
  sharedWatchlistCloneLimiter: { limit: mocks.sharedCloneLimit },
}));
vi.mock("@/lib/watchlist-slug", async () =>
  vi.importActual<typeof import("@/lib/watchlist-slug")>("@/lib/watchlist-slug"),
);
vi.mock("@/db/schema", () => ({
  watchlist: mocks.watchlist,
  watchlistCompany: mocks.watchlistCompany,
  company: {},
}));
vi.mock("@/db", () => ({ db: mocks.db }));
vi.mock("drizzle-orm", () => {
  const sql = (strings: TemplateStringsArray, ...values: unknown[]) => ({ strings, values });
  sql.join = (..._args: unknown[]) => ({ strings: [], values: [] });
  return {
    count: () => ({ kind: "count" }),
    eq: (...args: unknown[]) => ({ kind: "eq", args }),
    and: (...args: unknown[]) => ({ kind: "and", args }),
    sql,
  };
});

import {
  addCompanyToWatchlist,
  copyWatchlist,
  copySharedWatchlist,
  createWatchlist,
  shareWatchlist,
  updateWatchlist,
} from "@/lib/services/watchlists";

const USER_ID = "user-1";
const WATCHLIST_ID = "10000000-0000-4000-8000-000000000001";
const COMPANY_ID = "20000000-0000-4000-8000-000000000001";
const NEW_COMPANY_ID = "20000000-0000-4000-8000-000000000002";

beforeEach(() => {
  mocks.reset();
  mocks.getSessionUserId.mockResolvedValue(USER_ID);
  mocks.canCreateWatchlist.mockResolvedValue({ allowed: true });
  mocks.sharedCloneLimit.mockResolvedValue({ success: true });
});

describe("#3114 — watchlist multi-table writes are atomic", () => {
  it("rejects malformed scalar UUIDs before opening a membership transaction", async () => {
    await expect(addCompanyToWatchlist("not-a-uuid", COMPANY_ID)).resolves.toEqual({ ok: false });
    await expect(addCompanyToWatchlist(WATCHLIST_ID, "not-a-uuid")).resolves.toEqual({ ok: false });
    expect(mocks.calls.transactions).toBe(0);
  });

  it("enforces the 250-company membership ceiling under the parent-row transaction", async () => {
    mocks.setState({
      watchlists: [{
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: "source",
        title: "Source",
        isPublic: false,
        filters: {},
      }],
      companies: Array.from({ length: 250 }, (_, index) => ({
        watchlistId: WATCHLIST_ID,
        companyId: `20000000-0000-4000-8001-${index.toString().padStart(12, "0")}`,
      })),
    });

    mocks.queueRootSelect([{ userId: USER_ID }]);
    await expect(addCompanyToWatchlist(WATCHLIST_ID, NEW_COMPANY_ID)).resolves.toEqual({ ok: false, error: "company_limit_reached" });
    expect(mocks.snapshot().companies).toHaveLength(250);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 0 });
  });

  it("enables unlisted sharing only for the current owner and is idempotent", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    const source = {
      id: WATCHLIST_ID,
      userId: USER_ID,
      slug: "source",
      title: "Source",
      isPublic: true,
      shareEnabled: false,
      filters: {},
    };
    mocks.setState({ watchlists: [source], companies: [] });
    mocks.queueRootSelect(
      [{ id: WATCHLIST_ID, title: "Source", filters: {} }],
      [{ id: WATCHLIST_ID, title: "Source", filters: {} }],
    );

    await expect(shareWatchlist(WATCHLIST_ID)).resolves.toEqual({
      ok: true,
      url: `https://jseek.co/watchlists/${WATCHLIST_ID}`,
    });
    await expect(shareWatchlist(WATCHLIST_ID)).resolves.toEqual({
      ok: true,
      url: `https://jseek.co/watchlists/${WATCHLIST_ID}`,
    });

    expect(mocks.snapshot().watchlists[0]?.shareEnabled).toBe(true);
    expect(audit).toHaveBeenCalledTimes(1);
    audit.mockRestore();
  });

  it("does not enable sharing when the owner predicate returns no row", async () => {
    const source = {
      id: WATCHLIST_ID,
      userId: "different-owner",
      slug: "source",
      title: "Source",
      isPublic: true,
      shareEnabled: false,
      filters: {},
    };
    mocks.setState({ watchlists: [source], companies: [] });
    mocks.queueRootSelect([]);

    await expect(shareWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });
    expect(mocks.snapshot().watchlists[0]?.shareEnabled).toBe(false);
  });

  it.each([
    { title: "Bad\u0000title", filters: {} },
    { title: "Source", filters: { workMode: ["future-mode"] } },
  ])("does not enable sharing for an in-size but invalid legacy source", async ({ title, filters }) => {
    mocks.setState({
      watchlists: [{
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: "source",
        title,
        isPublic: false,
        shareEnabled: false,
        filters,
      }],
      companies: [],
    });

    await expect(shareWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "invalid_source" });
    expect(mocks.snapshot().watchlists[0]?.shareEnabled).toBe(false);
  });

  it("commits a private parent and company rows atomically", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    try {
      await expect(createWatchlist({
        title: "New watchlist",
        companyIds: [COMPANY_ID],
      })).resolves.toEqual({ id: "wl-new", slug: "new-watchlist" });

      expect(mocks.snapshot()).toEqual({
        watchlists: [expect.objectContaining({
          id: "wl-new",
          title: "New watchlist",
          isPublic: false,
        })],
        companies: [{ watchlistId: "wl-new", companyId: COMPANY_ID }],
      });
      expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 0 });
      expect(audit).toHaveBeenCalledTimes(1);
      expect(mocks.afterFn).not.toHaveBeenCalled();
    } finally {
      audit.mockRestore();
    }
  });

  it("retries a slug conflict in a fresh transaction and audits once", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    mocks.queueRootSelect([], [{ slug: "new-watchlist" }]);
    mocks.failSlugInserts(1);

    try {
      await expect(createWatchlist({
        title: "New watchlist",
        companyIds: [COMPANY_ID],
      })).resolves.toEqual({ id: "wl-new", slug: "new-watchlist-2" });

      expect(mocks.snapshot()).toEqual({
        watchlists: [expect.objectContaining({ id: "wl-new", slug: "new-watchlist-2" })],
        companies: [{ watchlistId: "wl-new", companyId: COMPANY_ID }],
      });
      expect(mocks.calls).toEqual({ transactions: 2, rollbacks: 1 });
      expect(audit).toHaveBeenCalledTimes(1);
      expect(mocks.afterFn).not.toHaveBeenCalled();
    } finally {
      audit.mockRestore();
    }
  });

  it("rejects a legacy public create before capacity or transaction work", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    try {
      await expect(createWatchlist({
        title: "Must stay private",
        companyIds: [COMPANY_ID],
        isPublic: true,
      })).resolves.toEqual({ error: "visibility_locked" });

      expect(mocks.snapshot()).toEqual({ watchlists: [], companies: [] });
      expect(mocks.calls).toEqual({ transactions: 0, rollbacks: 0 });
      expect(mocks.afterFn).not.toHaveBeenCalled();
      expect(audit).not.toHaveBeenCalled();
    } finally {
      audit.mockRestore();
    }
  });

  it("rolls back create when a company-row insert fails", async () => {
    mocks.failCompanyInserts();

    await expect(createWatchlist({
      title: "New watchlist",
      companyIds: [COMPANY_ID],
    })).rejects.toThrow("forced watchlist_company insert failure");

    expect(mocks.snapshot()).toEqual({ watchlists: [], companies: [] });
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("rejects the 11th direct create inside the insert transaction", async () => {
    mocks.setState({
      watchlists: Array.from({ length: 10 }, (_, index) => ({
        id: `wl-${index}`,
        userId: USER_ID,
        slug: `existing-${index}`,
        title: `Existing ${index}`,
        isPublic: false,
        filters: {},
      })),
      companies: [],
    });

    await expect(createWatchlist({
      title: "Eleventh",
      companyIds: [],
    })).resolves.toEqual({ error: "limit_reached" });

    expect(mocks.snapshot().watchlists).toHaveLength(10);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("preserves a grandfathered 16-watchlist account while blocking create", async () => {
    mocks.setState({
      watchlists: Array.from({ length: 16 }, (_, index) => ({
        id: `wl-${index}`,
        userId: USER_ID,
        slug: `existing-${index}`,
        title: `Existing ${index}`,
        isPublic: false,
        filters: {},
      })),
      companies: [],
    });

    await expect(createWatchlist({
      title: "Seventeenth",
      companyIds: [],
    })).resolves.toEqual({ error: "limit_reached" });

    expect(mocks.snapshot().watchlists).toHaveLength(16);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("still permits edits for a grandfathered 16-watchlist account", async () => {
    const existing = Array.from({ length: 16 }, (_, index) => ({
      id: index === 0 ? WATCHLIST_ID : `wl-${index}`,
      userId: USER_ID,
      slug: `existing-${index}`,
      title: `Existing ${index}`,
      isPublic: false,
      filters: {},
    }));
    mocks.setState({ watchlists: existing, companies: [] });
    mocks.queueRootSelect([{ ...existing[0] }]);

    await expect(updateWatchlist({
      watchlistId: WATCHLIST_ID,
      title: "Still editable",
    })).resolves.toEqual({ slug: "still-editable" });

    expect(mocks.snapshot().watchlists).toHaveLength(16);
    expect(mocks.snapshot().watchlists[0].title).toBe("Still editable");
  });

  it("preserves metadata and company membership when replacement fails", async () => {
    const originalState = {
      watchlists: [{
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: "existing",
        title: "Existing",
        isPublic: false,
        filters: {},
      }],
      companies: [{ watchlistId: WATCHLIST_ID, companyId: "company-old" }],
    };
    mocks.setState(originalState);
    mocks.queueRootSelect([{ ...originalState.watchlists[0] }]);
    mocks.failCompanyInserts();

    await expect(updateWatchlist({
      watchlistId: WATCHLIST_ID,
      title: "Changed title",
      companyIds: [NEW_COMPANY_ID],
    })).rejects.toThrow("forced watchlist_company insert failure");

    expect(mocks.snapshot()).toEqual(originalState);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it.each([
    { current: false, requested: true },
    { current: true, requested: false },
  ])(
    "rejects the visibility update from $current to $requested before mutation work",
    async ({ current, requested }) => {
      const originalState = {
        watchlists: [{
          id: WATCHLIST_ID,
          userId: USER_ID,
          slug: "existing",
          title: "Existing",
          isPublic: current,
          filters: {},
        }],
        companies: [],
      };
      mocks.setState(originalState);
      mocks.queueRootSelect([{ ...originalState.watchlists[0] }]);

      await expect(updateWatchlist({
        watchlistId: WATCHLIST_ID,
        title: "Must not partially apply",
        isPublic: requested,
      })).resolves.toEqual({ error: "visibility_locked" });

      expect(mocks.snapshot()).toEqual(originalState);
      expect(mocks.calls).toEqual({ transactions: 0, rollbacks: 0 });
      expect(mocks.afterFn).not.toHaveBeenCalled();
    },
  );

  it("rolls back the destination watchlist when copied company rows fail", async () => {
    const originalState = {
      watchlists: [{
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: "source",
        title: "Source",
        isPublic: false,
        filters: {},
      }],
      companies: [{ watchlistId: WATCHLIST_ID, companyId: "company-1" }],
    };
    mocks.setState(originalState);
    mocks.queueRootSelect([{ ...originalState.watchlists[0] }]);
    mocks.setNextWatchlistId("wl-copy");
    mocks.failCompanyInserts();

    await expect(copyWatchlist(WATCHLIST_ID)).rejects.toThrow(
      "forced watchlist_company insert failure",
    );

    expect(mocks.snapshot()).toEqual(originalState);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("fails closed for a cross-user source despite a legacy public flag", async () => {
    const source = {
      id: WATCHLIST_ID,
      userId: "source-owner",
      slug: "source",
      title: "Shared source",
      isPublic: true,
      filters: {},
    };
    const destinationRows = Array.from({ length: 9 }, (_, index) => ({
      id: `destination-${index}`,
      userId: USER_ID,
      slug: `destination-${index}`,
      title: `Destination ${index}`,
      isPublic: false,
      filters: {},
    }));
    mocks.setState({ watchlists: [source, ...destinationRows], companies: [] });
    mocks.queueRootSelect([{ ...source }]);
    await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({
      error: "not_found",
    });

    expect(mocks.snapshot().watchlists).toEqual([source, ...destinationRows]);
    expect(mocks.calls).toEqual({ transactions: 0, rollbacks: 0 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("clones an explicitly shared cross-user source as a private watchlist", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    const source = {
      id: WATCHLIST_ID,
      userId: "source-owner",
      slug: "source",
      title: "Shared source",
      isPublic: false,
      shareEnabled: true,
      filters: { keywords: ["platform"] },
    };
    mocks.setState({
      watchlists: [source],
      companies: [{ watchlistId: WATCHLIST_ID, companyId: "company-1" }],
    });
    mocks.queueRootSelect([{
      title: source.title,
      userId: source.userId,
      isShared: true,
    }]);
    mocks.setNextWatchlistId("shared-copy");

    try {
      await expect(copySharedWatchlist(WATCHLIST_ID)).resolves.toEqual({
        id: "shared-copy",
        slug: "shared-source",
      });
      expect(mocks.snapshot().watchlists).toContainEqual(expect.objectContaining({
        id: "shared-copy",
        userId: USER_ID,
        isPublic: false,
        shareEnabled: false,
        sourceWatchlistId: WATCHLIST_ID,
      }));
      const payload = JSON.parse(audit.mock.calls[0][0] as string) as Record<string, unknown>;
      expect(payload.copy_source_kind).toBe("share");
    } finally {
      audit.mockRestore();
    }
  });

  it("rejects a grandfathered public source that was never explicitly shared", async () => {
    mocks.queueRootSelect([{
      title: "Legacy public",
      userId: "source-owner",
      isShared: false,
    }]);

    await expect(copySharedWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });
    expect(mocks.calls).toEqual({ transactions: 0, rollbacks: 0 });
  });

  it("rate-limits shared clones inside the mutation boundary before reading the source", async () => {
    mocks.sharedCloneLimit.mockResolvedValue({ success: false });

    await expect(copySharedWatchlist(WATCHLIST_ID)).resolves.toEqual({
      error: "rate_limited",
    });
    expect(mocks.sharedCloneLimit).toHaveBeenCalledWith(USER_ID);
    expect(mocks.calls).toEqual({ transactions: 0, rollbacks: 0 });
  });

  it("rechecks share state under the source-row lock before cloning", async () => {
    const source = {
      id: WATCHLIST_ID,
      userId: "source-owner",
      slug: "source",
      title: "Revoked source",
      isPublic: false,
      shareEnabled: false,
      filters: {},
    };
    mocks.setState({ watchlists: [source], companies: [] });
    mocks.queueRootSelect([{
      title: source.title,
      userId: source.userId,
      isShared: true,
    }]);

    await expect(copySharedWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.snapshot().watchlists).toEqual([source]);
  });

  it("atomically duplicates an owned source with provenance and source-kind audit", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    const source = {
      id: WATCHLIST_ID,
      userId: USER_ID,
      slug: "source",
      title: "Owned source",
      isPublic: false,
      filters: { keywords: ["engineer"] },
    };
    const destinationRows = Array.from({ length: 8 }, (_, index) => ({
      id: `destination-${index}`,
      userId: USER_ID,
      slug: `destination-${index}`,
      title: `Destination ${index}`,
      isPublic: false,
      filters: {},
    }));
    mocks.setState({
      watchlists: [source, ...destinationRows],
      companies: [{ watchlistId: WATCHLIST_ID, companyId: "company-1" }],
    });
    mocks.queueRootSelect([{ ...source }]);
    mocks.setNextWatchlistId("wl-copy");

    try {
      await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({
        id: "wl-copy",
        slug: "owned-source",
      });

      const state = mocks.snapshot();
      expect(state.watchlists.filter((row) => row.userId === USER_ID)).toHaveLength(10);
      expect(state.watchlists).toContainEqual(expect.objectContaining({
        id: "wl-copy",
        userId: USER_ID,
        title: "Owned source",
        isPublic: false,
        filters: { keywords: ["engineer"] },
        sourceWatchlistId: WATCHLIST_ID,
      }));
      expect(state.companies).toContainEqual({
        watchlistId: "wl-copy",
        companyId: "company-1",
      });
      expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 0 });
      expect(mocks.afterFn).toHaveBeenCalledTimes(1);

      const payload = JSON.parse(audit.mock.calls[0][0] as string) as Record<string, unknown>;
      expect(payload).toMatchObject({
        action: "watchlist.copy",
        copy_source_kind: "owned",
        is_public_after: false,
      });
      expect(payload.watchlist_ref).toMatch(/^[0-9a-f]{12}$/);
      expect(JSON.stringify(payload)).not.toContain(USER_ID);
      expect(JSON.stringify(payload)).not.toContain("engineer");
      expect(JSON.stringify(payload)).not.toContain(WATCHLIST_ID);
      expect(JSON.stringify(payload)).not.toContain("wl-copy");
    } finally {
      audit.mockRestore();
    }
  });

  it("applies copy capacity to the destination owner independently of source authorization", async () => {
    const source = {
      id: WATCHLIST_ID,
      userId: USER_ID,
      slug: "source",
      title: "Shared source",
      isPublic: false,
      filters: {},
    };
    const destinationRows = Array.from({ length: 9 }, (_, index) => ({
      id: `destination-${index}`,
      userId: USER_ID,
      slug: `destination-${index}`,
      title: `Destination ${index}`,
      isPublic: false,
      filters: {},
    }));
    mocks.setState({ watchlists: [source, ...destinationRows], companies: [] });
    mocks.queueRootSelect([{ ...source }]);

    await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({
      error: "limit_reached",
    });

    expect(mocks.snapshot().watchlists).toEqual([source, ...destinationRows]);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
  });

  it("rechecks copy authorization inside the transaction", async () => {
    mocks.queueRootSelect([{
      id: WATCHLIST_ID,
      userId: USER_ID,
      slug: "source",
      title: "Source",
      isPublic: false,
      filters: {},
    }]);

    await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });

    expect(mocks.snapshot()).toEqual({ watchlists: [], companies: [] });
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("rejects a source whose ownership changes before the copy transaction", async () => {
    mocks.setState({
      watchlists: [{
        id: WATCHLIST_ID,
        userId: "different-owner",
        slug: "source",
        title: "Source",
        isPublic: true,
        filters: {},
      }],
      companies: [],
    });
    mocks.queueRootSelect([{
      userId: USER_ID,
      title: "Source",
      isPublic: false,
      filters: {},
    }]);

    await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });

    expect(mocks.snapshot().watchlists).toHaveLength(1);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });
});


describe("company reference mutation authorization and failure boundaries", () => {
  const owned = {
    id: WATCHLIST_ID, userId: USER_ID, slug: "existing", title: "Existing",
    isPublic: false, filters: {},
  };

  it("does not look up or materialize references for a different owner's watchlist", async () => {
    mocks.queueRootSelect([{ ...owned, userId: "other-user" }]);
    await expect(updateWatchlist({ watchlistId: WATCHLIST_ID, companyIds: [NEW_COMPANY_ID] })).resolves.toEqual({ error: "not_found" });
    expect(mocks.prepareCompanyReferences).not.toHaveBeenCalled();
    expect(mocks.persistCompanyReferences).not.toHaveBeenCalled();
    expect(mocks.calls.transactions).toBe(0);
  });

  it("rechecks ownership after preparation before materializing references", async () => {
    const revoked = { ...owned, userId: "other-user" };
    mocks.setState({ watchlists: [revoked], companies: [] });
    mocks.queueRootSelect([owned]);
    await expect(updateWatchlist({ watchlistId: WATCHLIST_ID, companyIds: [NEW_COMPANY_ID] })).resolves.toEqual({ error: "not_found" });
    expect(mocks.prepareCompanyReferences).toHaveBeenCalledWith([NEW_COMPANY_ID]);
    expect(mocks.persistCompanyReferences).not.toHaveBeenCalled();
    expect(mocks.snapshot()).toEqual({ watchlists: [revoked], companies: [] });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("preserves the original selection when canonical preparation fails", async () => {
    const initial = { watchlists: [owned], companies: [{ watchlistId: WATCHLIST_ID, companyId: COMPANY_ID }] };
    mocks.setState(initial);
    mocks.queueRootSelect([owned]);
    mocks.prepareCompanyReferences.mockRejectedValue({ code: "unknown_company" });
    await expect(updateWatchlist({ watchlistId: WATCHLIST_ID, companyIds: [COMPANY_ID, NEW_COMPANY_ID] })).resolves.toEqual({ error: "unknown_company" });
    expect(mocks.snapshot()).toEqual(initial);
    expect(mocks.calls.transactions).toBe(0);
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });
});
