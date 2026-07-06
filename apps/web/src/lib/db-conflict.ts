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
  if (!err || typeof err !== "object") return false;
  const e = err as { code?: unknown; constraint_name?: unknown };
  if (e.code !== "23505") return false;
  if (typeof e.constraint_name === "string") {
    return e.constraint_name === constraintName;
  }
  const message = (err as { message?: unknown }).message;
  return (
    typeof message === "string" && message.includes(constraintName)
  );
}
