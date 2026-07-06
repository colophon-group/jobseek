import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3201 — `generateUniqueSlug` is TOCTOU.
 *
 * Before the fix, `createWatchlist`/`copyWatchlist` ran two statements:
 *   1. `SELECT slug FROM watchlist WHERE user_id = ? AND slug LIKE ?`
 *   2. `INSERT INTO watchlist (user_id, slug, ...) VALUES (?, ?, ...)`
 *
 * Two concurrent callers with the same title (a double-fire of the
 * "Create" button or two tabs of the same modal) both observed an
 * empty SELECT and both attempted `INSERT slug = 'my-list'`. The
 * `idx_wl_user_slug` UNIQUE index caught the duplicate, but the loser
 * received an un-handled Postgres `23505` that bubbled out of the
 * server action as a 500.
 *
 * The fix wraps the INSERT in `insertWatchlistWithUniqueSlug`, which
 * catches `23505` on `idx_wl_user_slug`, re-runs `generateUniqueSlug`
 * (which now sees the winner's row), and retries up to 5 times.
 *
 * Test strategy
 * -------------
 * The unit-under-test is `insertWatchlistWithUniqueSlug`. Its only
 * collaborators are (a) the slug picker `generateUniqueSlug` and (b)
 * the inserter callback the caller passes in. We replace the `@/db`
 * module with an in-memory mock so we can:
 *
 *   - have the picker reflect "current" table state (other callers'
 *     winners are visible to the next pick),
 *   - have the inserter race against itself under `Promise.all` and
 *     reproduce the 23505 the index would raise in production,
 *   - assert that the retry loop converges on a unique slug.
 *
 * Calibration: a second describe-block runs the legacy buggy
 * `generateUniqueSlug` + INSERT shape (no retry) against the SAME
 * mock and asserts it crashes one of the concurrent callers. Without
 * this calibration the test could trivially pass for both shapes.
 */

vi.mock("server-only", () => ({}));

interface WatchlistRow {
  id: string;
  user_id: string;
  slug: string;
  title: string;
}

const mocks = vi.hoisted(() => {
  const watchlistTable: WatchlistRow[] = [];

  let idCounter = 0;
  const nextId = (): string => `wl-${++idCounter}`;

  const uniqueViolation = (slug: string): Error => {
    const e = new Error(
      `duplicate key value violates unique constraint "idx_wl_user_slug" (slug=${slug})`,
    ) as Error & { code: string; constraint_name: string };
    e.code = "23505";
    e.constraint_name = "idx_wl_user_slug";
    return e;
  };

  // Drizzle compiles `db.select(...).from(watchlist).where(and(eq(userId, userId), sql`LIKE ${prefix}%`))`
  // down through a fluent chain. The mock sniffs the parameters by
  // capturing whatever the action passes into `where(...)`. drizzle's
  // `eq` operator returns an `SQL` object with `.queryChunks`; `and`
  // wraps them in another SQL. We don't need to parse drizzle's AST
  // precisely — we walk every nested queryChunks and pluck out the
  // primitive Param values in encounter order. For `generateUniqueSlug`
  // the first two non-column-ref Params are `userId` (eq) and `base + "%"`
  // (LIKE).
  const dbSelect = () => {
    let userIdFilter: string | undefined;
    let likeStringFilter: string | undefined;
    const chain: Record<string, unknown> = {
      from: () => chain,
      where: (clause: unknown) => {
        const params = extractParams(clause);
        // generateUniqueSlug passes (userId, base + "%") — first string param is user, second is LIKE.
        const strs = params.filter((p): p is string => typeof p === "string");
        if (strs.length >= 1) userIdFilter = strs[0];
        if (strs.length >= 2) likeStringFilter = strs[1];
        return chain;
      },
    };
    chain.then = (
      resolve: (v: unknown) => unknown,
      _reject?: (e: unknown) => unknown,
    ) => {
      const u = userIdFilter;
      const likeStr = likeStringFilter ?? "";
      // postgres LIKE '%' wildcard at end → prefix match.
      const prefix = likeStr.endsWith("%") ? likeStr.slice(0, -1) : likeStr;
      const rows = u
        ? watchlistTable
            .filter((r) => r.user_id === u && r.slug.startsWith(prefix))
            .map((r) => ({ slug: r.slug }))
        : [];
      return resolve(rows);
    };
    return chain;
  };

  const dbInsert = () => ({
    values: (v: {
      userId: string;
      slug: string;
      title: string;
      [k: string]: unknown;
    }) => {
      const exec = async (): Promise<Array<{ id: string }>> => {
        // Yield once so two parallel calls can interleave their
        // generateUniqueSlug → INSERT pipelines.
        await Promise.resolve();
        const conflict = watchlistTable.some(
          (r) => r.user_id === v.userId && r.slug === v.slug,
        );
        if (conflict) throw uniqueViolation(v.slug);
        const id = nextId();
        watchlistTable.push({
          id,
          user_id: v.userId,
          slug: v.slug,
          title: v.title,
        });
        return [{ id }];
      };
      return {
        returning: () => exec(),
        then: (
          resolve: (val: unknown) => unknown,
          reject?: (err: unknown) => unknown,
        ) => exec().then(resolve, reject),
      };
    },
  });

  return {
    watchlistTable,
    reset: () => {
      watchlistTable.length = 0;
      idCounter = 0;
    },
    dbSelect,
    dbInsert,
    uniqueViolation,
  };
});

/**
 * Walk drizzle's `SQL` object (returned from `and`/`eq`/`sql`...) and
 * collect every primitive Param value, in encounter order. Param
 * objects look like `{ value: <primitive>, encoder: ... }`; nested
 * SQLs expose `.queryChunks`. StringChunks expose `.value: string[]`
 * (no encoder) — we skip them. Recurses one level via `cause` /
 * `queryChunks` to handle `and(...)` and tagged-template nesting.
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
      if (t === "string" || t === "number" || t === "bigint" || t === "boolean") {
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
        // Nested SQL — recurse.
        acc.push(...extractParams(chunk, depth + 1));
      }
    }
  }
  // Fallback: some operator returns expose `.value` directly (eq).
  const direct = (node as { value?: unknown }).value;
  if (direct !== undefined && typeof direct !== "object" && acc.length === 0) {
    acc.push(direct);
  }
  return acc;
}

vi.mock("@/db", () => ({
  db: {
    select: () => mocks.dbSelect(),
    insert: () => mocks.dbInsert(),
  },
}));

// ---- Module under test ---------------------------------------------

import {
  insertWatchlistWithUniqueSlug,
  generateUniqueSlug,
  isWatchlistSlugUniqueViolation,
} from "../watchlist-slug";

// Sanity check: confirm the mock wiring lets the real
// `generateUniqueSlug` observe our in-memory table. If this assertion
// fails, every downstream test is using stale params and the rest of
// the file is meaningless — fail fast and visibly.
describe("#3201 — test harness sanity", () => {
  beforeEach(() => {
    mocks.reset();
  });

  it("generateUniqueSlug sees the in-memory table via the mock", async () => {
    expect(await generateUniqueSlug("user-1", "My List")).toBe("my-list");

    mocks.watchlistTable.push({
      id: "seed",
      user_id: "user-1",
      slug: "my-list",
      title: "My List",
    });
    expect(await generateUniqueSlug("user-1", "My List")).toBe("my-list-2");

    mocks.watchlistTable.push({
      id: "seed2",
      user_id: "user-1",
      slug: "my-list-2",
      title: "My List",
    });
    expect(await generateUniqueSlug("user-1", "My List")).toBe("my-list-3");
  });

  it("isWatchlistSlugUniqueViolation only matches the targeted constraint", () => {
    expect(isWatchlistSlugUniqueViolation(mocks.uniqueViolation("x"))).toBe(true);
    const other = new Error("dup") as Error & { code: string; constraint_name: string };
    other.code = "23505";
    other.constraint_name = "some_other_unique";
    expect(isWatchlistSlugUniqueViolation(other)).toBe(false);
    expect(isWatchlistSlugUniqueViolation(new Error("network"))).toBe(false);
    // Message-fallback path (no constraint_name field, but message
    // contains the constraint name — covers drivers that omit the
    // structured field).
    const msgOnly = new Error(
      'duplicate key value violates unique constraint "idx_wl_user_slug"',
    ) as Error & { code: string };
    msgOnly.code = "23505";
    expect(isWatchlistSlugUniqueViolation(msgOnly)).toBe(true);
  });
});

describe("#3201 — insertWatchlistWithUniqueSlug retries on idx_wl_user_slug 23505", () => {
  beforeEach(() => {
    mocks.reset();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Reusable inserter — mirrors the production `createWatchlist` shape:
  // `db.insert(watchlist).values({...}).returning({id})`.
  function productionInserter(userId: string, title: string) {
    return async (slug: string): Promise<{ id: string }> => {
      const r = await mocks
        .dbInsert()
        .values({ userId, slug, title })
        .returning();
      return r[0];
    };
  }

  it("single insert succeeds with the picker's slug", async () => {
    const { row, slug } = await insertWatchlistWithUniqueSlug(
      "user-1",
      "My List",
      productionInserter("user-1", "My List"),
    );

    expect(slug).toBe("my-list");
    expect(row.id).toMatch(/^wl-/);
    expect(mocks.watchlistTable.map((r) => r.slug)).toEqual(["my-list"]);
  });

  it("picker auto-advances around a pre-existing row", async () => {
    mocks.watchlistTable.push({
      id: "wl-seed",
      user_id: "user-1",
      slug: "my-list",
      title: "My List",
    });

    const { slug } = await insertWatchlistWithUniqueSlug(
      "user-1",
      "My List",
      productionInserter("user-1", "My List"),
    );

    expect(slug).toBe("my-list-2");
    expect(mocks.watchlistTable.map((r) => r.slug).sort()).toEqual([
      "my-list",
      "my-list-2",
    ]);
  });

  it("retries on 23505 from a racing winner and lands on -2", async () => {
    // Custom inserter that throws 23505 on the first attempt to
    // simulate a racing caller landing their commit between this
    // call's SELECT and INSERT. We seed the winner's row before
    // throwing so the retry's picker observes the conflict and
    // advances to `-2`.
    let attempts = 0;
    const inserter = async (candidate: string): Promise<{ id: string }> => {
      attempts += 1;
      if (attempts === 1) {
        // Winner committed.
        mocks.watchlistTable.push({
          id: "wl-winner",
          user_id: "user-1",
          slug: candidate,
          title: "My List",
        });
        throw mocks.uniqueViolation(candidate);
      }
      return (
        await mocks
          .dbInsert()
          .values({ userId: "user-1", slug: candidate, title: "My List" })
          .returning()
      )[0];
    };

    const { slug } = await insertWatchlistWithUniqueSlug(
      "user-1",
      "My List",
      inserter,
    );

    expect(attempts).toBe(2);
    expect(slug).toBe("my-list-2");
    const slugs = mocks.watchlistTable
      .filter((r) => r.user_id === "user-1")
      .map((r) => r.slug)
      .sort();
    expect(slugs).toEqual(["my-list", "my-list-2"]);
    // The whole point of the fix: every slug is unique within the
    // user's namespace, even under contention.
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("two concurrent inserts produce distinct slugs (the headline guarantee)", async () => {
    const inserter = productionInserter("user-1", "My List");

    // Under Promise.all the two SELECTs both see an empty table and
    // both pick "my-list"; one INSERT wins, the other throws 23505;
    // the loser's retry sees the winner's row and picks "my-list-2".
    const [r1, r2] = await Promise.all([
      insertWatchlistWithUniqueSlug("user-1", "My List", inserter),
      insertWatchlistWithUniqueSlug("user-1", "My List", inserter),
    ]);

    expect(r1.slug).not.toBe(r2.slug);
    const slugs = [r1.slug, r2.slug].sort();
    expect(slugs).toEqual(["my-list", "my-list-2"]);

    const tableSlugs = mocks.watchlistTable
      .filter((r) => r.user_id === "user-1")
      .map((r) => r.slug)
      .sort();
    expect(tableSlugs).toEqual(["my-list", "my-list-2"]);
    expect(new Set(tableSlugs).size).toBe(tableSlugs.length);
  });

  it("non-slug 23505 (different constraint) propagates immediately", async () => {
    const otherUniqueErr = new Error(
      'duplicate key value violates unique constraint "some_other_unique"',
    ) as Error & { code: string; constraint_name: string };
    otherUniqueErr.code = "23505";
    otherUniqueErr.constraint_name = "some_other_unique";

    let calls = 0;
    await expect(
      insertWatchlistWithUniqueSlug("user-1", "My List", async () => {
        calls += 1;
        throw otherUniqueErr;
      }),
    ).rejects.toThrow(/some_other_unique/);
    // No retry — proves the predicate is narrow.
    expect(calls).toBe(1);
  });

  it("non-unique-violation errors propagate immediately without retry", async () => {
    let calls = 0;
    await expect(
      insertWatchlistWithUniqueSlug("user-1", "My List", async () => {
        calls += 1;
        throw new Error("ECONNRESET");
      }),
    ).rejects.toThrow(/ECONNRESET/);
    expect(calls).toBe(1);
  });

  it("exhausts the retry budget if every attempt conflicts and surfaces the violation", async () => {
    // The picker always sees an empty table because we don't add to
    // it. Every inserter call throws 23505. The retry loop runs the
    // configured budget (5 attempts) then surfaces the last error.
    let calls = 0;
    await expect(
      insertWatchlistWithUniqueSlug("user-1", "My List", async (candidate) => {
        calls += 1;
        throw mocks.uniqueViolation(candidate);
      }),
    ).rejects.toMatchObject({ code: "23505" });
    expect(calls).toBeGreaterThanOrEqual(2);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Calibration: prove the harness can distinguish fixed from broken by
// running the legacy buggy algorithm (raw SELECT + INSERT, no retry)
// against the SAME mock and asserting it crashes one of the concurrent
// callers. Without this the suite could pass for both shapes.
// ─────────────────────────────────────────────────────────────────────

describe("#3201 — race-mock calibration (buggy shape crashes loser)", () => {
  beforeEach(() => {
    mocks.reset();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("buggy SELECT-then-INSERT shape throws 23505 under Promise.all", async () => {
    const buggyCreateWatchlist = async (
      userId: string,
      title: string,
    ): Promise<{ slug: string }> => {
      // Step 1: pick a slug (the legacy code path).
      const slug = await generateUniqueSlug(userId, title);
      // Step 2: insert. NO retry, NO unique-violation catch.
      const [r] = await mocks
        .dbInsert()
        .values({ userId, slug, title })
        .returning();
      return { slug, ...r };
    };

    await expect(
      Promise.all([
        buggyCreateWatchlist("user-1", "My List"),
        buggyCreateWatchlist("user-1", "My List"),
      ]),
    ).rejects.toMatchObject({ code: "23505" });

    // The winner's row still landed.
    const slugs = mocks.watchlistTable
      .filter((r) => r.user_id === "user-1")
      .map((r) => r.slug);
    expect(slugs).toEqual(["my-list"]);
  });
});
