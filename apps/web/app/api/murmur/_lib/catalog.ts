/**
 * Catalog writer for the Murmur webhook accept handler.
 *
 * The handler ingests `final_output` (`FinalOutput` from
 * `accept-schema.ts`) and writes the company + boards to one of two
 * backends, chosen by `MURMUR_ACCEPT_TARGET`:
 *
 *   - `"postgres"` (default) — INSERT into `company` and `job_board`,
 *      wrapped in the same transaction as the `murmur_accept_log`
 *      ledger row.
 *   - `"csv"` — append rows to `apps/crawler/data/companies.csv` and
 *      `apps/crawler/data/boards.csv`. Demo / operator-side debug only;
 *      no concurrency control.
 *
 * Either backend exposes the same `applyCatalog` function; the route
 * handler doesn't know which one is active. On Postgres-side conflicts
 * (slug exists, board_url exists) the EXISTING row wins and the
 * conflict is recorded as a non-fatal warning.
 *
 * @see colophon-group/jobseek#2763
 * @see Murmur DESIGN.md §4.2 (Storage migration on jobseek's side)
 */

import path from "node:path";
import fs from "node:fs/promises";

import type { FinalOutput, FinalOutputBoard } from "./accept-schema";

/** The two backends the handler supports. */
export type CatalogTarget = "postgres" | "csv";

/** What the handler returns to the route handler on success/failure. */
export interface ApplyCatalogResult {
  /** UUID of the company row, or null when the CSV backend is active. */
  readonly companyId: string | null;
  /** Number of boards persisted (after dedupe). */
  readonly boardCount: number;
  /** Non-fatal anomalies. Empty on a clean apply. */
  readonly warnings: readonly string[];
}

/** Side-channel data the catalog writer needs to record the ledger row. */
export interface ApplyCatalogContext {
  readonly runId: string;
  readonly bodyHash: string;
}

const TARGET_ENV = "MURMUR_ACCEPT_TARGET" as const;

/**
 * Resolve the active backend from the env. Defaults to `"postgres"`.
 * Unknown / empty values fall through to the default.
 */
export function resolveCatalogTarget(): CatalogTarget {
  const v = process.env[TARGET_ENV]?.trim().toLowerCase();
  if (v === "csv") return "csv";
  return "postgres";
}

export type ApplyCatalog = (
  target: CatalogTarget,
  body: FinalOutput,
  context: ApplyCatalogContext,
) => Promise<ApplyCatalogResult>;

/**
 * Production catalog writer.
 *
 * The Postgres branch is wrapped in a transaction that:
 *   1. Inserts the ledger row (PRIMARY KEY on run_id). On conflict,
 *      the function throws `CatalogIdempotencyConflict` — the route
 *      catches that and surfaces `already_applied`.
 *   2. Upserts the company by slug.
 *   3. Upserts each `job_board` by board_url.
 *   4. Backfills the ledger row with `companyId` + `boardCount`.
 *
 * The CSV branch appends raw rows to `apps/crawler/data/companies.csv`
 * and `apps/crawler/data/boards.csv` in the existing column order.
 */
export const defaultApplyCatalog: ApplyCatalog = async (
  target,
  body,
  context,
) => {
  if (target === "csv") {
    return applyCsv(body, context);
  }
  return applyPostgres(body, context);
};

/**
 * Mutable holder for the active applier (mirrors `InvokerHolder` from
 * the J5 invoker). Tests overwrite `current` with a stub before
 * exercising the route; production code never reassigns it.
 */
export const ApplyCatalogHolder: { current: ApplyCatalog } = {
  current: defaultApplyCatalog,
};

/** Convenience pass-through used by the route. */
export function applyCatalog(
  target: CatalogTarget,
  body: FinalOutput,
  context: ApplyCatalogContext,
): Promise<ApplyCatalogResult> {
  return ApplyCatalogHolder.current(target, body, context);
}

/**
 * Thrown by the Postgres branch if a concurrent writer beat us to the
 * ledger row. The route catches this and surfaces `already_applied`.
 */
export class CatalogIdempotencyConflict extends Error {
  constructor(public readonly runId: string) {
    super(`catalog: concurrent insert on run_id=${runId}`);
    this.name = "CatalogIdempotencyConflict";
  }
}

// ── Postgres backend ──────────────────────────────────────────────

async function applyPostgres(
  body: FinalOutput,
  context: ApplyCatalogContext,
): Promise<ApplyCatalogResult> {
  // The DB module lives at `@/db`, but we import lazily so unit tests
  // (which stub `applyCatalog` outright) never construct a connection
  // and never need DATABASE_URL.
  const { db } = await import("@/db");
  const { company, jobBoard, murmurAcceptLog } = await import("@/db/schema");
  const { eq, sql } = await import("drizzle-orm");

  const warnings: string[] = [];
  let companyId: string | null = null;
  const target: CatalogTarget = "postgres";

  await db.transaction(async (tx) => {
    // 1. Ledger row first — UNIQUE on run_id is the idempotency gate.
    //    On conflict (concurrent first-write), throw so the route can
    //    surface "already_applied" rather than write twice.
    const ledgerInsert = await tx
      .insert(murmurAcceptLog)
      .values({
        runId: context.runId,
        bodySha256: context.bodyHash,
        companyId: null,
        boardCount: 0,
        target,
      })
      .onConflictDoNothing({ target: murmurAcceptLog.runId })
      .returning({ runId: murmurAcceptLog.runId });

    if (ledgerInsert.length === 0) {
      throw new CatalogIdempotencyConflict(context.runId);
    }

    // 2. Company upsert — INSERT, fall back to existing on slug
    //    collision.
    const insertedCompany = await tx
      .insert(company)
      .values({
        name: body.canonical_name,
        slug: body.slug,
        website: body.canonical_website,
        description: body.description,
        extras: { industry_ids: body.industry_ids },
      })
      .onConflictDoNothing({ target: company.slug })
      .returning({ id: company.id });

    if (insertedCompany.length > 0 && insertedCompany[0]) {
      companyId = insertedCompany[0].id;
    } else {
      const existing = await tx
        .select({ id: company.id })
        .from(company)
        .where(eq(company.slug, body.slug))
        .limit(1);
      if (existing.length === 0 || !existing[0]) {
        throw new Error(
          `catalog: slug ${body.slug} insert was a no-op but no existing row found`,
        );
      }
      companyId = existing[0].id;
      warnings.push("slug_conflict");
    }

    // 3. Boards — one per entry, dedupe by board_url.
    let written = 0;
    for (const b of body.boards) {
      const insertedBoard = await tx
        .insert(jobBoard)
        .values({
          companyId,
          boardSlug: deriveBoardSlug(body.slug, b),
          boardUrl: b.board_url,
          crawlerType: b.monitor_type,
          metadata: {
            provider: b.provider,
            hreflang: b.hreflang ?? null,
            monitor_config: b.monitor_config,
            scraper_type: b.scraper_type,
            scraper_config: b.scraper_config,
            verdict: b.verdict,
            per_field: b.per_field ?? null,
          },
        })
        .onConflictDoNothing({ target: jobBoard.boardUrl })
        .returning({ id: jobBoard.id });
      if (insertedBoard.length > 0) {
        written += 1;
      } else {
        warnings.push(`board_url_conflict:${b.alias}`);
      }
    }

    // 4. Backfill the ledger row with the populated stats.
    await tx
      .update(murmurAcceptLog)
      .set({
        companyId,
        boardCount: sql`${written}`,
      })
      .where(eq(murmurAcceptLog.runId, context.runId));
  });

  return {
    companyId,
    boardCount: body.boards.length,
    warnings,
  };
}

function deriveBoardSlug(companySlug: string, board: FinalOutputBoard): string {
  // Same convention as `apps/crawler/data/boards.csv`:
  // `<company>-<provider>`, optionally suffixed when there are multiple
  // boards per provider.
  const base = `${companySlug}-${board.provider}`;
  if (board.alias && board.alias !== "global" && board.alias !== companySlug) {
    return `${base}-${slugify(board.alias)}`;
  }
  return base;
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// ── CSV backend ──────────────────────────────────────────────────

const CSV_DIR_ENV = "MURMUR_ACCEPT_CSV_DIR";

async function applyCsv(
  body: FinalOutput,
  _context: ApplyCatalogContext,
): Promise<ApplyCatalogResult> {
  const dir =
    process.env[CSV_DIR_ENV] ??
    path.resolve(process.cwd(), "../crawler/data");

  const companiesCsv = path.join(dir, "companies.csv");
  const boardsCsv = path.join(dir, "boards.csv");

  // companies.csv columns:
  //   slug,name,website,logo_url,icon_url,logo_type,industry,
  //   employee_count_range,founded_year,extras
  const companyRow =
    csvJoin([
      body.slug,
      body.canonical_name,
      body.canonical_website,
      "",
      "",
      "",
      body.industry_ids.join("|"),
      "",
      "",
      JSON.stringify({ description: body.description }),
    ]) + "\n";
  await fs.appendFile(companiesCsv, companyRow, "utf8");

  // boards.csv columns:
  //   company_slug,board_slug,board_url,monitor_type,monitor_config,
  //   scraper_type,scraper_config
  const boardRows = body.boards
    .map((b) =>
      csvJoin([
        body.slug,
        deriveBoardSlug(body.slug, b),
        b.board_url,
        b.monitor_type,
        JSON.stringify(b.monitor_config),
        b.scraper_type,
        JSON.stringify(b.scraper_config),
      ]),
    )
    .join("\n");
  await fs.appendFile(boardsCsv, boardRows + "\n", "utf8");

  return {
    companyId: null,
    boardCount: body.boards.length,
    warnings: [],
  };
}

function csvJoin(fields: readonly string[]): string {
  return fields.map(csvField).join(",");
}

function csvField(s: string): string {
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}
