/**
 * Postgres unique-violation (SQLSTATE `23505`) helpers.
 *
 * Two distinct call-sites in `apps/web/src/lib/actions/` need to detect
 * a unique-violation scoped to a specific index:
 *
 *   - `toggleSavedJob` (#3179) — `idx_sj_user_posting` on saved_job.
 *   - `toggleStarredCompany` (#3179) — `idx_fc_user_company` on
 *     followed_company.
 *
 * The legacy pattern from #3268 (`watchlist-slug.ts`) inlined the
 * detection logic for `idx_wl_user_slug`. Two more callers crossed the
 * "would-be-helpful" line, so the recognition logic moved here. The
 * watchlist-slug helper still maintains its own copy of the predicate
 * to keep that PR's surface unchanged.
 *
 * Why a constraint-name filter (rather than bare `code === "23505"`):
 * an unscoped retry would absorb conflicts on unrelated indices and
 * mask the underlying bug. Requiring the constraint name keeps the
 * catch narrow — any other unique violation propagates so the real
 * failure surfaces.
 *
 * Drivers vary in whether they expose `constraint_name` as a top-level
 * field: postgres.js (used here) does, but some proxy layers strip it.
 * Fall back to substring-matching the message in that case — postgres
 * prints the constraint name verbatim in the human-readable error.
 */

/**
 * Returns true iff `err` is a Postgres `unique_violation` (SQLSTATE
 * 23505) whose violated constraint is `constraintName`.
 */
export function isUniqueViolation(
  err: unknown,
  constraintName: string,
): boolean {
  // Drizzle wraps postgres.js errors in a query error whose `cause`
  // carries the SQLSTATE and constraint metadata. Production therefore
  // sees `{ cause: { code: "23505", constraint_name: ... } }`, while the
  // unit-test and direct-driver shapes expose those fields at the top
  // level. Walk a short, cycle-safe cause chain so both shapes are
  // recognized without treating unrelated nested errors as conflicts.
  let current: unknown = err;
  const seen = new Set<object>();

  for (let depth = 0; depth < 5; depth++) {
    if (!current || typeof current !== "object" || seen.has(current)) {
      return false;
    }
    seen.add(current);

    const e = current as {
      code?: unknown;
      constraint_name?: unknown;
      message?: unknown;
      cause?: unknown;
    };

    if (e.code === "23505") {
      if (typeof e.constraint_name === "string") {
        return e.constraint_name === constraintName;
      }
      if (typeof e.message === "string" && e.message.includes(constraintName)) {
        return true;
      }
    }

    current = e.cause;
  }

  return false;
}
