/**
 * Idempotency helpers for the Murmur webhook accept handler.
 *
 * Murmur sends `Idempotency-Key: <run_id>` (per DESIGN.md §4.1) and
 * retries non-2xx responses once after 30s. The accept handler must
 * therefore:
 *
 *   1. Hash the canonical-JSON form of the body so re-fires can be
 *      classified as "same body" vs "different body".
 *   2. Look up `(run_id)` in the `murmur_accept_log` ledger.
 *   3. Decide one of three outcomes:
 *        - `fresh`         — never seen this run_id; proceed to apply.
 *        - `already`       — same hash as the existing row; idempotent
 *                            success, skip apply, return
 *                            `applied: false, reason: "already_applied"`.
 *        - `body_mismatch` — different hash; the first body is the
 *                            source of truth, log a warning and return
 *                            `applied: false, reason: "body_mismatch"`.
 *
 * The ledger insert and the catalog write are wrapped in the same
 * transaction so a crash between them never leaves a half-applied
 * state.
 *
 * @see colophon-group/jobseek#2763
 */

/**
 * Stable, length-prefixed canonical form of a JSON value. Two bodies
 * that decode to the same JS object always produce the same string;
 * key order and whitespace differences are removed.
 *
 * The output is fed directly into SHA-256.
 */
export function canonicalizeJson(_value: unknown): string {
  throw new Error("not implemented");
}

/** Hex SHA-256 of the canonicalised JSON form. */
export function sha256Canonical(_value: unknown): string {
  throw new Error("not implemented");
}

/** Outcome of classifying an inbound run_id + body against the ledger. */
export type LedgerCheck =
  | { readonly status: "fresh" }
  | { readonly status: "already_applied"; readonly companyId: string | null }
  | { readonly status: "body_mismatch"; readonly companyId: string | null };

/**
 * Read-side ledger probe. Implementations consult
 * `murmur_accept_log` and return one of the three statuses above.
 *
 * Tests provide a stub that returns whichever status the case wants.
 */
export type LedgerReader = (
  runId: string,
  bodyHash: string,
) => Promise<LedgerCheck>;

/**
 * Write-side ledger commit. Inserts the row alongside the catalog
 * write inside the same transaction. Throws on UNIQUE-constraint
 * violation (the caller upgrades that to `already_applied`).
 */
export type LedgerWriter = (entry: {
  readonly runId: string;
  readonly bodyHash: string;
  readonly companyId: string | null;
  readonly boardCount: number;
  readonly target: string;
}) => Promise<void>;
