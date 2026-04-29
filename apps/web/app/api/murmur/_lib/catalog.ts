/**
 * Catalog writer for the Murmur webhook accept handler.
 *
 * The handler ingests `final_output` (`FinalOutput` from
 * `accept-schema.ts`) and writes the company + boards to one of two
 * backends, chosen by `MURMUR_ACCEPT_TARGET`:
 *
 *   - `"postgres"` (default) — INSERT into the `company` and `job_board`
 *      tables, wrapped in the same transaction as the
 *      `murmur_accept_log` ledger row.
 *   - `"csv"` — append rows to `apps/crawler/data/companies.csv` and
 *      `apps/crawler/data/boards.csv`. Demo / operator-side debug only;
 *      no concurrency control.
 *
 * Either backend exposes the same `applyCatalog` function; the route
 * handler doesn't know which one is active. On Postgres-side conflicts
 * (slug already exists, board_url already exists) the writer follows
 * the existing jobseek convention: the EXISTING row wins, and the
 * conflict is recorded as a `errors:` token but NOT a hard failure
 * (the run is still considered applied — the company is in the catalog).
 *
 * @see colophon-group/jobseek#2763
 * @see Murmur DESIGN.md §4.2 (Storage migration on jobseek's side)
 */

import type { FinalOutput } from "./accept-schema";

/** The two backends the handler supports. */
export type CatalogTarget = "postgres" | "csv";

/** What the handler returns to the route handler on success/failure. */
export interface ApplyCatalogResult {
  /** UUID of the company row, or null when the CSV backend is active. */
  readonly companyId: string | null;
  /** Number of boards persisted (after dedupe). */
  readonly boardCount: number;
  /**
   * Non-fatal anomalies surfaced in the response envelope as
   * `errors: ["catalog:<token>"]`. Examples: `slug_conflict`,
   * `board_url_conflict`. Empty array on a clean apply.
   */
  readonly warnings: readonly string[];
}

/**
 * Resolve the active backend from the env. Defaults to `"postgres"`.
 * Unknown / empty values fall through to the default.
 */
export function resolveCatalogTarget(): CatalogTarget {
  throw new Error("not implemented");
}

/**
 * Apply the validated `final_output` to the active catalog backend.
 *
 * Contract:
 *   - Throws on unrecoverable I/O errors (DB unavailable, file system
 *     unwriteable). The route handler catches and maps to
 *     `errors: ["catalog_write_failed"]` per the M0 envelope.
 *   - Returns the structured result on success — including non-fatal
 *     warnings the handler should surface.
 *   - When `target === "postgres"`, the implementation is responsible
 *     for opening a transaction that also writes the
 *     `murmur_accept_log` row via the supplied `ledgerWriter`. This
 *     keeps the catalog write and the idempotency-ledger update
 *     atomic.
 *
 * Tests inject a stub that bypasses the DB / FS entirely. The route
 * handler in production passes `defaultApplyCatalog`.
 */
export type ApplyCatalog = (
  target: CatalogTarget,
  body: FinalOutput,
  context: ApplyCatalogContext,
) => Promise<ApplyCatalogResult>;

/** Side-channel data the catalog writer needs to record the ledger row. */
export interface ApplyCatalogContext {
  readonly runId: string;
  readonly bodyHash: string;
}

/** Production implementation. Defined in `catalog-default.ts`. */
export const defaultApplyCatalog: ApplyCatalog = async () => {
  throw new Error("not implemented");
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
