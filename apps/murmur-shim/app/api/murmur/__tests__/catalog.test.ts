/**
 * Unit tests for `defaultApplyCatalog` (the Postgres + CSV branches of
 * `apps/murmur-shim/app/api/murmur/_lib/catalog.ts`).
 *
 * Background:
 *   The route-level tests in `accept.test.ts` stub `ApplyCatalogHolder
 *   .current` outright, which means the production `defaultApplyCatalog`
 *   never runs there. This file fills that gap. It exercises the
 *   transactional ledger-first ordering, slug / board_url conflict
 *   handling, the CSV branch's append + ledger-CSV write, and the
 *   `CatalogIdempotencyConflict` race path.
 *
 * Strategy:
 *   - **Postgres branch:** mock `@/db`, `@/db/schema`, and `drizzle-orm`
 *     with an in-memory store that respects UNIQUE on `run_id`,
 *     `company.slug`, and `job_board.board_url`. The mock follows the
 *     same pattern as `apps/murmur-shim/src/lib/murmur/claim-kv.test.ts`.
 *   - **CSV branch:** drive against `os.tmpdir()` and read the appended
 *     rows + the `murmur_accept_log.csv` sidecar.
 *
 * @see colophon-group/jobseek#2763
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";

// ── Postgres branch ────────────────────────────────────────────────

interface CompanyRow {
  id: string;
  name: string;
  slug: string;
  website: string;
  description: string;
  extras: unknown;
}
interface JobBoardRow {
  id: string;
  companyId: string;
  boardSlug: string;
  boardUrl: string;
  crawlerType: string;
  metadata: unknown;
}
interface LedgerRow {
  runId: string;
  bodySha256: string;
  companyId: string | null;
  boardCount: number;
  target: string;
}

interface MockState {
  companies: Map<string, CompanyRow>; // keyed by slug
  boards: Map<string, JobBoardRow>; // keyed by board_url
  ledger: Map<string, LedgerRow>; // keyed by run_id
  nextCompanyId: number;
  nextBoardId: number;
  /** When set, the next ledger insert pretends a UNIQUE-conflict raced. */
  forceLedgerConflict: boolean;
}

const state: MockState = {
  companies: new Map(),
  boards: new Map(),
  ledger: new Map(),
  nextCompanyId: 1,
  nextBoardId: 1,
  forceLedgerConflict: false,
};

function newCompanyId(): string {
  const n = state.nextCompanyId++;
  return `00000000-0000-0000-0000-${n.toString(16).padStart(12, "0")}`;
}
function newBoardId(): string {
  const n = state.nextBoardId++;
  return `00000000-0000-0000-0000-board-${n}`.slice(0, 36);
}

vi.mock("@/db", () => {
  // Each chain returns a thenable so `await tx.insert(...).values(...).
  // onConflictDoNothing({...}).returning({...})` resolves to the inserted
  // rows array. We model JUST the calls catalog.ts makes — anything
  // else throws so silent drift is caught.
  type ColumnRef = { _table: string; _column: string };
  type EqNode = { _col: ColumnRef; _val: unknown };

  function isLedgerCol(c: ColumnRef): boolean {
    return c._table === "murmur_accept_log";
  }
  function isCompanyCol(c: ColumnRef): boolean {
    return c._table === "company";
  }

  function buildTx() {
    const tx = {
      insert: (table: { _name: string }) => {
        return {
          values: (vals: Record<string, unknown>) => {
            const onConflictDoNothing = (_target?: unknown) => {
              const returning = (_shape: unknown) => {
                // Apply the insert depending on table.
                if (table._name === "murmur_accept_log") {
                  if (state.forceLedgerConflict) {
                    state.forceLedgerConflict = false;
                    return Promise.resolve([]);
                  }
                  if (state.ledger.has(vals.runId as string)) {
                    return Promise.resolve([]);
                  }
                  state.ledger.set(vals.runId as string, {
                    runId: vals.runId as string,
                    bodySha256: vals.bodySha256 as string,
                    companyId: (vals.companyId as string | null) ?? null,
                    boardCount: (vals.boardCount as number) ?? 0,
                    target: vals.target as string,
                  });
                  return Promise.resolve([{ runId: vals.runId as string }]);
                }
                if (table._name === "company") {
                  if (state.companies.has(vals.slug as string)) {
                    return Promise.resolve([]);
                  }
                  const id = newCompanyId();
                  state.companies.set(vals.slug as string, {
                    id,
                    name: vals.name as string,
                    slug: vals.slug as string,
                    website: vals.website as string,
                    description: vals.description as string,
                    extras: vals.extras,
                  });
                  return Promise.resolve([{ id }]);
                }
                if (table._name === "job_board") {
                  if (state.boards.has(vals.boardUrl as string)) {
                    return Promise.resolve([]);
                  }
                  const id = newBoardId();
                  state.boards.set(vals.boardUrl as string, {
                    id,
                    companyId: vals.companyId as string,
                    boardSlug: vals.boardSlug as string,
                    boardUrl: vals.boardUrl as string,
                    crawlerType: vals.crawlerType as string,
                    metadata: vals.metadata,
                  });
                  return Promise.resolve([{ id }]);
                }
                throw new Error(`unsupported insert into ${table._name}`);
              };
              return { returning };
            };
            return { onConflictDoNothing };
          },
        };
      },
      select: (_shape: unknown) => ({
        from: (table: { _name: string }) => ({
          where: (cond: EqNode) => ({
            limit: (_n: number) => {
              if (
                table._name === "company" &&
                cond._col._column === "slug"
              ) {
                const row = state.companies.get(cond._val as string);
                return Promise.resolve(row ? [{ id: row.id }] : []);
              }
              throw new Error(
                `unsupported select from ${table._name} on ${cond._col._column}`,
              );
            },
          }),
        }),
      }),
      update: (table: { _name: string }) => ({
        set: (sets: Record<string, unknown>) => ({
          where: (cond: EqNode) => {
            if (
              table._name === "murmur_accept_log" &&
              cond._col._column === "run_id"
            ) {
              const row = state.ledger.get(cond._val as string);
              if (row) {
                state.ledger.set(cond._val as string, {
                  ...row,
                  companyId: (sets.companyId as string | null) ?? row.companyId,
                  boardCount: (sets.boardCount as number) ?? row.boardCount,
                });
              }
              return Promise.resolve();
            }
            throw new Error(
              `unsupported update on ${table._name} / ${cond._col._column}`,
            );
          },
        }),
      }),
    };
    // Type-assert to cover the columns referenced via `.target` in the
    // production code. The mock ignores them — we just need a noop.
    void isLedgerCol;
    void isCompanyCol;
    return tx;
  }

  const db = {
    transaction: async <T>(fn: (tx: ReturnType<typeof buildTx>) => Promise<T>): Promise<T> => {
      return fn(buildTx());
    },
  };
  return { db };
});

vi.mock("@/db/schema", () => {
  // Each schema export is referenced both as a "table" (for
  // `tx.insert(...)`) and as a column dictionary (for
  // `tx.update(...).where(eq(murmurAcceptLog.runId, ...))`). We give
  // each table a `_name` and each column a `_table` + `_column`.
  const col = (table: string, column: string) => ({
    _table: table,
    _column: column,
  });

  const company = {
    _name: "company",
    id: col("company", "id"),
    slug: col("company", "slug"),
  };
  const jobBoard = {
    _name: "job_board",
    id: col("job_board", "id"),
    boardUrl: col("job_board", "board_url"),
  };
  const murmurAcceptLog = {
    _name: "murmur_accept_log",
    runId: col("murmur_accept_log", "run_id"),
  };
  return { company, jobBoard, murmurAcceptLog };
});

vi.mock("drizzle-orm", () => {
  const eq = (
    col: { _table: string; _column: string },
    val: unknown,
  ) => ({ _col: col, _val: val });
  return { eq };
});

// Now import the module under test (after mocks are registered).
import { defaultApplyCatalog, CatalogIdempotencyConflict } from "../_lib/catalog";
import type { FinalOutput } from "../_lib/accept-schema";

const FINAL_OUTPUT: FinalOutput = {
  canonical_name: "Acme Co",
  canonical_website: "https://acme.example.com",
  slug: "acme",
  description: "Test company.",
  industry_ids: ["software"],
  boards: [
    {
      alias: "global",
      board_url: "https://job-boards.greenhouse.io/acme",
      provider: "greenhouse",
      outcome: "configured",
      monitor_type: "greenhouse",
      monitor_config: { token: "acme" },
      scraper_type: "skip",
      scraper_config: {},
      verdict: "ok",
    },
  ],
};

beforeEach(() => {
  state.companies.clear();
  state.boards.clear();
  state.ledger.clear();
  state.nextCompanyId = 1;
  state.nextBoardId = 1;
  state.forceLedgerConflict = false;
});

describe("defaultApplyCatalog (Postgres)", () => {
  it("writes ledger + company + boards in one transaction", async () => {
    const result = await defaultApplyCatalog("postgres", FINAL_OUTPUT, {
      runId: "run-pg-1",
      bodyHash: "hash-pg-1",
    });
    expect(result.companyId).toBeTruthy();
    expect(result.boardCount).toBe(1);
    expect(result.warnings).toEqual([]);

    const ledgerRow = state.ledger.get("run-pg-1");
    expect(ledgerRow).toBeTruthy();
    expect(ledgerRow?.bodySha256).toBe("hash-pg-1");
    expect(ledgerRow?.target).toBe("postgres");
    // Backfilled after company + board inserts:
    expect(ledgerRow?.companyId).toBe(result.companyId);
    expect(ledgerRow?.boardCount).toBe(1);

    expect(state.companies.get("acme")?.name).toBe("Acme Co");
    expect(
      state.boards.get("https://job-boards.greenhouse.io/acme")?.boardSlug,
    ).toBe("acme-greenhouse");
  });

  it("slug conflict: existing company wins, slug_conflict warning recorded", async () => {
    // Pre-seed an existing company at the same slug.
    state.companies.set("acme", {
      id: "00000000-0000-0000-0000-existing0001",
      name: "Pre-existing Acme",
      slug: "acme",
      website: "https://acme.example.com",
      description: "older",
      extras: {},
    });
    const result = await defaultApplyCatalog("postgres", FINAL_OUTPUT, {
      runId: "run-pg-2",
      bodyHash: "hash-pg-2",
    });
    expect(result.companyId).toBe("00000000-0000-0000-0000-existing0001");
    expect(result.warnings).toContain("slug_conflict");
  });

  it("board_url conflict: existing board wins, warning per alias recorded", async () => {
    state.boards.set("https://job-boards.greenhouse.io/acme", {
      id: "00000000-0000-0000-0000-existingBd1",
      companyId: "00000000-0000-0000-0000-existing0002",
      boardSlug: "acme-greenhouse",
      boardUrl: "https://job-boards.greenhouse.io/acme",
      crawlerType: "greenhouse",
      metadata: {},
    });
    const result = await defaultApplyCatalog("postgres", FINAL_OUTPUT, {
      runId: "run-pg-3",
      bodyHash: "hash-pg-3",
    });
    expect(result.warnings).toContain("board_url_conflict:global");
  });

  it("CatalogIdempotencyConflict when ledger UNIQUE-races (returning() === [])", async () => {
    state.forceLedgerConflict = true;
    await expect(
      defaultApplyCatalog("postgres", FINAL_OUTPUT, {
        runId: "run-pg-race",
        bodyHash: "hash-pg-race",
      }),
    ).rejects.toBeInstanceOf(CatalogIdempotencyConflict);
    // No catalog rows written.
    expect(state.companies.size).toBe(0);
    expect(state.boards.size).toBe(0);
    // No ledger row written either (the empty `returning()` short-
    // circuits before company / board inserts).
    expect(state.ledger.size).toBe(0);
  });

  it("UNIQUE constraint on (run_id): second apply with same run_id throws CatalogIdempotencyConflict", async () => {
    // First apply lands cleanly.
    const r1 = await defaultApplyCatalog("postgres", FINAL_OUTPUT, {
      runId: "run-pg-unique",
      bodyHash: "hash-pg-unique",
    });
    expect(r1.companyId).toBeTruthy();
    expect(state.ledger.size).toBe(1);

    // Second apply with the same run_id — the in-memory store's UNIQUE
    // semantics return [] from the ledger insert, which `applyPostgres`
    // upgrades to CatalogIdempotencyConflict.
    await expect(
      defaultApplyCatalog("postgres", FINAL_OUTPUT, {
        runId: "run-pg-unique",
        bodyHash: "hash-pg-unique-2",
      }),
    ).rejects.toBeInstanceOf(CatalogIdempotencyConflict);
    // Ledger size unchanged.
    expect(state.ledger.size).toBe(1);
  });
});

// ── CSV branch ─────────────────────────────────────────────────────

describe("defaultApplyCatalog (CSV)", () => {
  let tmpDir: string;
  let prevEnv: string | undefined;
  let prevTarget: string | undefined;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "murmur-csv-"));
    prevEnv = process.env.MURMUR_ACCEPT_CSV_DIR;
    prevTarget = process.env.MURMUR_ACCEPT_TARGET;
    process.env.MURMUR_ACCEPT_CSV_DIR = tmpDir;
    process.env.MURMUR_ACCEPT_TARGET = "csv";
  });

  afterEach(async () => {
    if (prevEnv === undefined) delete process.env.MURMUR_ACCEPT_CSV_DIR;
    else process.env.MURMUR_ACCEPT_CSV_DIR = prevEnv;
    if (prevTarget === undefined) delete process.env.MURMUR_ACCEPT_TARGET;
    else process.env.MURMUR_ACCEPT_TARGET = prevTarget;
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("appends company + board rows and writes the durable ledger CSV", async () => {
    const result = await defaultApplyCatalog("csv", FINAL_OUTPUT, {
      runId: "run-csv-1",
      bodyHash: "hash-csv-1",
    });
    expect(result.companyId).toBeNull();
    expect(result.boardCount).toBe(1);
    expect(result.warnings).toEqual([]);

    const companies = await fs.readFile(
      path.join(tmpDir, "companies.csv"),
      "utf8",
    );
    expect(companies).toMatch(/^acme,Acme Co,https:\/\/acme\.example\.com,/);

    const boards = await fs.readFile(
      path.join(tmpDir, "boards.csv"),
      "utf8",
    );
    expect(boards).toMatch(/acme,acme-greenhouse,https:\/\/job-boards/);

    // Durable ledger sidecar is the cold-start idempotency guard.
    const ledger = await fs.readFile(
      path.join(tmpDir, "murmur_accept_log.csv"),
      "utf8",
    );
    expect(ledger).toMatch(/^run-csv-1,hash-csv-1,csv,/);
  });

  it("cold-start replay (CSV): second apply with same run_id throws CatalogIdempotencyConflict", async () => {
    // First apply lands. Writes companies.csv, boards.csv, ledger CSV.
    await defaultApplyCatalog("csv", FINAL_OUTPUT, {
      runId: "run-csv-replay",
      bodyHash: "hash-csv-replay",
    });
    const companiesBefore = await fs.readFile(
      path.join(tmpDir, "companies.csv"),
      "utf8",
    );

    // Simulate a process restart — no in-memory state is kept by the
    // catalog module, but the durable CSV ledger remains. The second
    // call must surface CatalogIdempotencyConflict before appending
    // duplicate rows.
    await expect(
      defaultApplyCatalog("csv", FINAL_OUTPUT, {
        runId: "run-csv-replay",
        bodyHash: "hash-csv-replay",
      }),
    ).rejects.toBeInstanceOf(CatalogIdempotencyConflict);

    const companiesAfter = await fs.readFile(
      path.join(tmpDir, "companies.csv"),
      "utf8",
    );
    // No duplicate row was appended.
    expect(companiesAfter).toBe(companiesBefore);
  });

  it("cold-start body_mismatch (CSV): different hash for same run_id throws CatalogIdempotencyConflict", async () => {
    await defaultApplyCatalog("csv", FINAL_OUTPUT, {
      runId: "run-csv-mismatch",
      bodyHash: "hash-csv-A",
    });
    await expect(
      defaultApplyCatalog("csv", FINAL_OUTPUT, {
        runId: "run-csv-mismatch",
        bodyHash: "hash-csv-B",
      }),
    ).rejects.toBeInstanceOf(CatalogIdempotencyConflict);
  });
});
