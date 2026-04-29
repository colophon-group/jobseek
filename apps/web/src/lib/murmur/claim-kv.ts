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

import { and, eq } from "drizzle-orm";
import { db } from "@/db";
import { murmurClaimKv } from "@/db/schema";

/**
 * Fetch a single named value for a claim.
 *
 * @param claim_token - The opaque token identifying the claim row.
 * @param name - The named-config slot.
 * @returns The previously-stored value, or `null` if no row exists for
 *   this `(claim_token, name)` pair.
 */
export async function getKV(
  claim_token: string,
  name: string,
): Promise<unknown | null> {
  const rows = await db
    .select({ value: murmurClaimKv.value })
    .from(murmurClaimKv)
    .where(
      and(
        eq(murmurClaimKv.claimToken, claim_token),
        eq(murmurClaimKv.name, name),
      ),
    )
    .limit(1);

  if (rows.length === 0) return null;
  return rows[0]!.value as unknown;
}

/**
 * Store a named value for a claim. Performs an UPSERT keyed on
 * `(claim_token, name)`: if a row already exists it is overwritten and
 * `updated_at` is bumped to the current time.
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
  claim_token: string,
  name: string,
  value: unknown,
): Promise<void> {
  await db
    .insert(murmurClaimKv)
    .values({
      claimToken: claim_token,
      name,
      value: value as never,
    })
    .onConflictDoUpdate({
      target: [murmurClaimKv.claimToken, murmurClaimKv.name],
      set: {
        value: value as never,
        updatedAt: new Date(),
      },
    });
}

/**
 * List all named values for a claim.
 *
 * @param claim_token - The opaque token identifying the claim row.
 * @returns An object mapping each `name` to its stored value. Empty when
 *   the token has no rows.
 */
export async function listKV(
  claim_token: string,
): Promise<Record<string, unknown>> {
  const rows = await db
    .select({ name: murmurClaimKv.name, value: murmurClaimKv.value })
    .from(murmurClaimKv)
    .where(eq(murmurClaimKv.claimToken, claim_token));

  const out: Record<string, unknown> = {};
  for (const row of rows) {
    out[row.name] = row.value as unknown;
  }
  return out;
}

/**
 * Remove every named value belonging to a claim. Other claims are
 * unaffected.
 *
 * @param claim_token - The opaque token identifying the claim row.
 */
export async function clearKV(claim_token: string): Promise<void> {
  await db
    .delete(murmurClaimKv)
    .where(eq(murmurClaimKv.claimToken, claim_token));
}
