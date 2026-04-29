/**
 * Tests for claim-kv.ts.
 *
 * The repo convention (see lib/actions/__tests__/bootstrap.test.ts) is to
 * mock `@/db` rather than spin up a live Postgres. We mirror that pattern,
 * but to faithfully exercise the verification list from jobseek#2757 we
 * back the mock with an in-memory store that respects the table's
 * `(claim_token, name)` primary key and the UPSERT semantics promised by
 * the production module.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("server-only", () => ({}));

interface Row {
  claim_token: string;
  name: string;
  value: unknown;
  created_at: Date;
  updated_at: Date;
}

// Keyed by `${claim_token}\x00${name}` to match the composite PK.
const store = new Map<string, Row>();
const k = (token: string, name: string) => `${token}\x00${name}`;

interface FakeQuery {
  claim_token?: string;
  name?: string;
  value?: unknown;
  op: "select" | "insert" | "delete";
  fields?: "value" | "name+value";
  conflictUpsert?: boolean;
}

// Reset between tests so each case starts from a clean store.
beforeEach(() => {
  store.clear();
});

// The mock parses the chained Drizzle builder into an intent object,
// then runs it against the in-memory store at await time.
vi.mock("@/db", () => {
  function execute(q: FakeQuery): Promise<unknown> {
    return new Promise((resolve) => {
      // Microtask delay so concurrent callers genuinely interleave.
      queueMicrotask(() => {
        if (q.op === "select") {
          const out: Array<Record<string, unknown>> = [];
          for (const row of store.values()) {
            if (q.claim_token !== undefined && row.claim_token !== q.claim_token) continue;
            if (q.name !== undefined && row.name !== q.name) continue;
            if (q.fields === "value") out.push({ value: row.value });
            else out.push({ name: row.name, value: row.value });
          }
          resolve(out);
          return;
        }
        if (q.op === "insert") {
          const key = k(q.claim_token!, q.name!);
          const now = new Date();
          const existing = store.get(key);
          if (existing && q.conflictUpsert) {
            existing.value = q.value;
            existing.updated_at = now;
          } else if (!existing) {
            store.set(key, {
              claim_token: q.claim_token!,
              name: q.name!,
              value: q.value,
              created_at: now,
              updated_at: now,
            });
          }
          // If existing && !conflictUpsert, a real PG would throw. The
          // production module always sets conflictUpsert=true, so this
          // branch is unreachable from claim-kv.ts.
          resolve(undefined);
          return;
        }
        if (q.op === "delete") {
          for (const [key, row] of store.entries()) {
            if (q.claim_token !== undefined && row.claim_token !== q.claim_token) continue;
            store.delete(key);
          }
          resolve(undefined);
          return;
        }
        resolve(undefined);
      });
    });
  }

  // Drizzle builders under test:
  // - select({...}).from(table).where(and(eq(claim_token,t), eq(name,n))).limit(1)
  // - select({name,value}).from(table).where(eq(claim_token,t))
  // - insert(table).values({...}).onConflictDoUpdate({target:[claim_token,name], set:{...}})
  // - delete(table).where(eq(claim_token,t))
  //
  // Our condition objects are tagged so the mock can introspect them.
  const selectBuilder = (fieldShape: "value" | "name+value") => ({
    from: () => ({
      where: (cond: { token?: string; name?: string }) => {
        const queryTail = {
          claim_token: cond.token,
          name: cond.name,
          op: "select" as const,
          fields: fieldShape,
        };
        const promise = execute(queryTail);
        return Object.assign(promise, {
          limit: () => execute(queryTail),
        });
      },
    }),
  });

  const db = {
    select: (shape: { value?: unknown; name?: unknown }) => {
      const fieldShape: "value" | "name+value" =
        "name" in shape ? "name+value" : "value";
      return selectBuilder(fieldShape);
    },
    insert: () => ({
      values: (v: { claimToken: string; name: string; value: unknown }) => {
        const onConflictDoUpdate = (_args: unknown) =>
          execute({
            op: "insert",
            claim_token: v.claimToken,
            name: v.name,
            value: v.value,
            conflictUpsert: true,
          });
        const insertPromise = execute({
          op: "insert",
          claim_token: v.claimToken,
          name: v.name,
          value: v.value,
          conflictUpsert: false,
        });
        return Object.assign(insertPromise, { onConflictDoUpdate });
      },
    }),
    delete: () => ({
      where: (cond: { token?: string }) =>
        execute({ op: "delete", claim_token: cond.token }),
    }),
  };

  return { db };
});

// drizzle-orm helpers: the real `eq`/`and` build SQL operator nodes.
// Our mock doesn't run real SQL, so we model them as plain objects that
// the fake builder can introspect.
vi.mock("drizzle-orm", () => {
  // `eq` produces a tagged node whose role depends on context: when nested
  // inside `and(...)`, the `_col`/`_val` fields are aggregated; when used
  // bare in `.where(eq(...))`, the node also carries `token`/`name`
  // shortcut fields so the fake builder can read them directly.
  const eq = (col: { _name: string }, val: unknown) => {
    const node: {
      _col: string;
      _val: unknown;
      token?: string;
      name?: string;
    } = { _col: col._name, _val: val };
    if (col._name === "claim_token") node.token = val as string;
    if (col._name === "name") node.name = val as string;
    return node;
  };

  const and = (
    ...nodes: Array<{ _col: string; _val: unknown }>
  ) => {
    const out: { token?: string; name?: string } = {};
    for (const n of nodes) {
      if (n._col === "claim_token") out.token = n._val as string;
      if (n._col === "name") out.name = n._val as string;
    }
    return out;
  };

  return { eq, and };
});

// The schema columns are referenced by name; our drizzle-orm mock keys
// off `_name`, so expose just enough for the production module to compile.
vi.mock("@/db/schema", () => ({
  murmurClaimKv: {
    claimToken: { _name: "claim_token" },
    name: { _name: "name" },
    value: { _name: "value" },
  },
}));

// Now import the module under test (after the mocks are registered).
import { getKV, setKV, listKV, clearKV } from "./claim-kv";

describe("claim-kv", () => {
  it("setKV then getKV returns the same value", async () => {
    await setKV("tok-1", "config-a", { foo: "bar", n: 42 });
    const got = await getKV("tok-1", "config-a");
    expect(got).toEqual({ foo: "bar", n: 42 });
  });

  it("setKV overwrite under same (token, name) updates updated_at and overwrites value (UPSERT)", async () => {
    await setKV("tok-2", "config-a", "first");
    const before = store.get(k("tok-2", "config-a"))!;
    const beforeUpdated = before.updated_at;

    // Force a measurable wall-clock gap so the timestamp comparison is
    // unambiguous even on fast machines.
    await new Promise((r) => setTimeout(r, 5));

    await setKV("tok-2", "config-a", "second");

    const after = store.get(k("tok-2", "config-a"))!;
    expect(after.value).toBe("second");
    expect(after.updated_at.getTime()).toBeGreaterThan(beforeUpdated.getTime());
    // created_at must NOT change on UPSERT.
    expect(after.created_at.getTime()).toBe(before.created_at.getTime());

    // And the public getter reflects the overwrite.
    expect(await getKV("tok-2", "config-a")).toBe("second");
  });

  it("getKV for unknown token/name returns null", async () => {
    expect(await getKV("missing-token", "missing-name")).toBeNull();
    await setKV("tok-3", "x", 1);
    expect(await getKV("tok-3", "y")).toBeNull();
    expect(await getKV("other-token", "x")).toBeNull();
  });

  it("listKV returns all (name -> value) pairs for a given token", async () => {
    await setKV("tok-4", "a", 1);
    await setKV("tok-4", "b", { nested: true });
    await setKV("tok-4", "c", "three");
    // Different token must NOT be included.
    await setKV("tok-other", "a", "noise");

    const all = await listKV("tok-4");
    expect(all).toEqual({ a: 1, b: { nested: true }, c: "three" });
  });

  it("clearKV removes all rows for a token; other tokens unaffected", async () => {
    await setKV("tok-5", "a", 1);
    await setKV("tok-5", "b", 2);
    await setKV("tok-keep", "a", "keep-me");

    await clearKV("tok-5");

    expect(await listKV("tok-5")).toEqual({});
    expect(await getKV("tok-5", "a")).toBeNull();
    expect(await getKV("tok-keep", "a")).toBe("keep-me");
  });

  it("concurrent setKV calls under same (token, name) last-write-wins (no error)", async () => {
    // Fire 50 setKV calls in parallel against the same key; the resolution
    // order is non-deterministic from the caller's POV, but the contract
    // is: no throws, exactly one row remains, the row's value is one of
    // the inputs.
    const inputs = Array.from({ length: 50 }, (_, i) => `v${i}`);
    await expect(
      Promise.all(inputs.map((v) => setKV("tok-6", "shared", v))),
    ).resolves.toBeDefined();

    const final = (await getKV("tok-6", "shared")) as string;
    expect(inputs).toContain(final);

    // Exactly one row, not 50.
    let count = 0;
    for (const row of store.values()) {
      if (row.claim_token === "tok-6" && row.name === "shared") count++;
    }
    expect(count).toBe(1);
  });

  it("stores JSON-serializable values without extra encoding (jsonb round-trip)", async () => {
    const cases: unknown[] = [
      null,
      true,
      0,
      "",
      "hello",
      42.5,
      [1, 2, 3],
      { a: { b: { c: [true, false] } } },
    ];

    for (let i = 0; i < cases.length; i++) {
      const v = cases[i];
      await setKV("tok-7", `case-${i}`, v);
    }

    for (let i = 0; i < cases.length; i++) {
      const round = await getKV("tok-7", `case-${i}`);
      // jsonb should preserve identity for these JSON-safe types — the
      // module must not wrap or stringify them.
      expect(round).toEqual(cases[i]);
    }
  });
});
