import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => {
  type WatchlistRow = {
    id: string;
    userId: string;
    slug: string;
    title: string;
    description: string | null;
    isPublic: boolean;
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
    description: { __column: "watchlist.description" },
    isPublic: { __column: "watchlist.isPublic" },
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
          description: (value.description as string | null) ?? null,
          isPublic: Boolean(value.isPublic),
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
    const chain: Record<string, unknown> = {};
    chain.set = (values: Record<string, unknown>) => {
      updates = values;
      return chain;
    };
    chain.where = () => {
      if (table !== watchlist) throw new Error("unexpected update table");
      Object.assign(state.watchlists[0], updates);
      return Promise.resolve([]);
    };
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
    update: () => {
      throw new Error("mutation escaped transaction");
    },
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
  };

  return {
    watchlist,
    watchlistCompany,
    db,
    calls,
    afterFn,
    getSessionUserId,
    canCreateWatchlist,
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
vi.mock("next/cache", () => ({ updateTag: vi.fn() }));
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));
vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: mocks.canCreateWatchlist,
  getUserPlan: vi.fn(),
  PLAN_LIMITS: { free: {}, paid: {} },
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
  copyWatchlist,
  createWatchlist,
  updateWatchlist,
} from "@/lib/services/watchlists";

const USER_ID = "user-1";
const WATCHLIST_ID = "wl-existing";

beforeEach(() => {
  mocks.reset();
  mocks.getSessionUserId.mockResolvedValue(USER_ID);
  mocks.canCreateWatchlist.mockResolvedValue({ allowed: true });
});

describe("#3114 — watchlist multi-table writes are atomic", () => {
  it("commits parent and company rows before registering post-commit work", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    try {
      await expect(createWatchlist({
        title: "New watchlist",
        companyIds: ["company-1"],
        isPublic: true,
      })).resolves.toEqual({ id: "wl-new", slug: "new-watchlist" });

      expect(mocks.snapshot()).toEqual({
        watchlists: [expect.objectContaining({ id: "wl-new", title: "New watchlist" })],
        companies: [{ watchlistId: "wl-new", companyId: "company-1" }],
      });
      expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 0 });
      expect(audit).toHaveBeenCalledTimes(1);
      expect(mocks.afterFn).toHaveBeenCalledTimes(2);
    } finally {
      audit.mockRestore();
    }
  });

  it("retries a slug conflict in a fresh transaction and emits hooks once", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    mocks.queueRootSelect([], [{ slug: "new-watchlist" }]);
    mocks.failSlugInserts(1);

    try {
      await expect(createWatchlist({
        title: "New watchlist",
        companyIds: ["company-1"],
        isPublic: true,
      })).resolves.toEqual({ id: "wl-new", slug: "new-watchlist-2" });

      expect(mocks.snapshot()).toEqual({
        watchlists: [expect.objectContaining({ id: "wl-new", slug: "new-watchlist-2" })],
        companies: [{ watchlistId: "wl-new", companyId: "company-1" }],
      });
      expect(mocks.calls).toEqual({ transactions: 2, rollbacks: 1 });
      expect(audit).toHaveBeenCalledTimes(1);
      expect(mocks.afterFn).toHaveBeenCalledTimes(2);
    } finally {
      audit.mockRestore();
    }
  });

  it("rolls back create when a company-row insert fails", async () => {
    mocks.failCompanyInserts();

    await expect(createWatchlist({
      title: "New watchlist",
      companyIds: ["company-1"],
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
        description: null,
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
        description: null,
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
      description: null,
      isPublic: false,
      filters: {},
    }));
    mocks.setState({ watchlists: existing, companies: [] });
    mocks.queueRootSelect([{ ...existing[0] }]);

    await expect(updateWatchlist({
      watchlistId: WATCHLIST_ID,
      description: "Still editable",
    })).resolves.toEqual({ slug: "existing-0" });

    expect(mocks.snapshot().watchlists).toHaveLength(16);
    expect(mocks.snapshot().watchlists[0].description).toBe("Still editable");
  });

  it("preserves metadata and company membership when replacement fails", async () => {
    const originalState = {
      watchlists: [{
        id: WATCHLIST_ID,
        userId: USER_ID,
        slug: "existing",
        title: "Existing",
        description: "Original description",
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
      description: "Changed description",
      companyIds: ["company-new"],
    })).rejects.toThrow("forced watchlist_company insert failure");

    expect(mocks.snapshot()).toEqual(originalState);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("rolls back the destination watchlist when copied company rows fail", async () => {
    const originalState = {
      watchlists: [{
        id: WATCHLIST_ID,
        userId: "source-owner",
        slug: "source",
        title: "Source",
        description: null,
        isPublic: true,
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

  it("copies a cross-user public source when the destination has capacity", async () => {
    const audit = vi.spyOn(console, "info").mockImplementation(() => {});
    const source = {
      id: WATCHLIST_ID,
      userId: "source-owner",
      slug: "source",
      title: "Shared source",
      description: null,
      isPublic: true,
      filters: {},
    };
    const destinationRows = Array.from({ length: 9 }, (_, index) => ({
      id: `destination-${index}`,
      userId: USER_ID,
      slug: `destination-${index}`,
      title: `Destination ${index}`,
      description: null,
      isPublic: false,
      filters: {},
    }));
    mocks.setState({ watchlists: [source, ...destinationRows], companies: [] });
    mocks.queueRootSelect([{ ...source }]);
    mocks.setNextWatchlistId("wl-copy");

    try {
      await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({
        id: "wl-copy",
        slug: "shared-source",
      });

      const rows = mocks.snapshot().watchlists;
      expect(rows.filter((row) => row.userId === USER_ID)).toHaveLength(10);
      expect(rows).toContainEqual(expect.objectContaining({
        id: "wl-copy",
        userId: USER_ID,
        title: "Shared source",
        sourceWatchlistId: WATCHLIST_ID,
      }));
    } finally {
      audit.mockRestore();
    }
  });

  it("applies copy capacity to the destination owner, not the cross-user source", async () => {
    const source = {
      id: WATCHLIST_ID,
      userId: "source-owner",
      slug: "source",
      title: "Shared source",
      description: null,
      isPublic: true,
      filters: {},
    };
    const destinationRows = Array.from({ length: 10 }, (_, index) => ({
      id: `destination-${index}`,
      userId: USER_ID,
      slug: `destination-${index}`,
      title: `Destination ${index}`,
      description: null,
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
      userId: "source-owner",
      slug: "source",
      title: "Source",
      description: null,
      isPublic: true,
      filters: {},
    }]);

    await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });

    expect(mocks.snapshot()).toEqual({ watchlists: [], companies: [] });
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });

  it("rejects a source that becomes private before the copy transaction", async () => {
    mocks.setState({
      watchlists: [{
        id: WATCHLIST_ID,
        userId: "source-owner",
        slug: "source",
        title: "Source",
        description: null,
        isPublic: false,
        filters: {},
      }],
      companies: [],
    });
    mocks.queueRootSelect([{
      userId: "source-owner",
      title: "Source",
      description: null,
      isPublic: true,
      filters: {},
    }]);

    await expect(copyWatchlist(WATCHLIST_ID)).resolves.toEqual({ error: "not_found" });

    expect(mocks.snapshot().watchlists).toHaveLength(1);
    expect(mocks.calls).toEqual({ transactions: 1, rollbacks: 1 });
    expect(mocks.afterFn).not.toHaveBeenCalled();
  });
});
