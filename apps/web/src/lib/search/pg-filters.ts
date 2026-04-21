import { sql } from "drizzle-orm";

/**
 * Postgres clause that filters `job_posting` (aliased `jp`) to postings
 * whose `locales` array overlaps the given languages — including postings
 * with no detected language (`cardinality = 0`). Mirrors the Typesense
 * `_none` sentinel semantics so Typesense and Postgres paths produce the
 * same result set.
 *
 * Returns `null` when no language filter is active; callers should skip
 * appending a clause rather than emitting a trivially-true predicate.
 */
export function localesOrNoneClause(languages: string[] | undefined) {
  if (!languages || languages.length === 0) return null;
  const arr = `{${languages.join(",")}}`;
  return sql`(jp.locales && ${arr}::text[] OR cardinality(jp.locales) = 0)`;
}
