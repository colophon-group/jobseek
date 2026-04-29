/**
 * Per-claim KV store for jobseek's named-config state.
 *
 * Backed by the Postgres table `murmur_claim_kv`, keyed on
 * `(claim_token, name)`. All values must be JSON-serializable; they are
 * stored in a `jsonb` column so they round-trip through Drizzle without any
 * additional encoding.
 *
 * Only this module is permitted to issue queries against
 * `murmur_claim_kv`. All callers must go through these four functions.
 *
 * @see colophon-group/jobseek#2757
 */

/**
 * Fetch a single named value for a claim.
 *
 * @param claim_token - The opaque token identifying the claim row.
 * @param name - The named-config slot.
 * @returns The previously-stored value, or `null` if no row exists for
 *   this `(claim_token, name)` pair.
 */
export async function getKV(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  claim_token: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  name: string,
): Promise<unknown | null> {
  throw new Error("not implemented");
}

/**
 * Store a named value for a claim. Performs an UPSERT keyed on
 * `(claim_token, name)`: if a row already exists it is overwritten and
 * `updated_at` is bumped to the current transaction time.
 *
 * Concurrent writes under the same `(claim_token, name)` are last-write-
 * wins via Postgres' row-level locking — the function never throws on
 * conflict.
 *
 * @param claim_token - The opaque token identifying the claim row.
 * @param name - The named-config slot.
 * @param value - Any JSON-serializable value.
 */
export async function setKV(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  claim_token: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  name: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  value: unknown,
): Promise<void> {
  throw new Error("not implemented");
}

/**
 * List all named values for a claim.
 *
 * @param claim_token - The opaque token identifying the claim row.
 * @returns An object mapping each `name` to its stored value. Empty when
 *   the token has no rows.
 */
export async function listKV(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  claim_token: string,
): Promise<Record<string, unknown>> {
  throw new Error("not implemented");
}

/**
 * Remove every named value belonging to a claim. Other claims are
 * unaffected.
 *
 * @param claim_token - The opaque token identifying the claim row.
 */
export async function clearKV(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  claim_token: string,
): Promise<void> {
  throw new Error("not implemented");
}
