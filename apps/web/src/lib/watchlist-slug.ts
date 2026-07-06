import "server-only";

import { eq, and, sql } from "drizzle-orm";
import { db } from "@/db";
import { watchlist } from "@/db/schema";

export function slugifyTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/\+/g, "plus")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export async function generateUniqueSlug(
  userId: string,
  title: string,
): Promise<string> {
  const base = slugifyTitle(title) || "watchlist";

  const rows = await db
    .select({ slug: watchlist.slug })
    .from(watchlist)
    .where(
      and(
        eq(watchlist.userId, userId),
        sql`${watchlist.slug} LIKE ${base + "%"}`,
      ),
    );

  const existing = new Set(rows.map((r) => r.slug));
  if (!existing.has(base)) return base;

  let i = 2;
  while (existing.has(`${base}-${i}`)) i++;
  return `${base}-${i}`;
}

/**
 * Name of the UNIQUE index that backs `(user_id, slug)` on the
 * `watchlist` table (see `apps/web/src/db/schema.ts`). Used to scope
 * the retry helper to only the conflict it knows how to recover from —
 * any other unique violation (e.g. a future column gains its own
 * constraint) propagates unchanged so the underlying bug surfaces
 * instead of being silently retried.
 */
export const WATCHLIST_SLUG_UNIQUE_CONSTRAINT = "idx_wl_user_slug";

/**
 * Detect Postgres `unique_violation` (SQLSTATE 23505) errors that hit
 * the `(user_id, slug)` index. postgres.js surfaces `code` and
 * `constraint_name` on the thrown Error; drizzle and Vercel's runtime
 * pass these through verbatim. We check both: `code === "23505"` alone
 * is too broad (would absorb conflicts on unrelated indices); requiring
 * the constraint name keeps the retry narrow.
 */
export function isWatchlistSlugUniqueViolation(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as { code?: unknown; constraint_name?: unknown };
  if (e.code !== "23505") return false;
  // Some drivers / proxies might omit constraint_name. If absent, fall
  // back to a substring match against the message (postgres prints the
  // constraint name verbatim in the human-readable message).
  if (typeof e.constraint_name === "string") {
    return e.constraint_name === WATCHLIST_SLUG_UNIQUE_CONSTRAINT;
  }
  const message = (err as { message?: unknown }).message;
  return typeof message === "string"
    && message.includes(WATCHLIST_SLUG_UNIQUE_CONSTRAINT);
}

/**
 * Maximum number of insert attempts before giving up. The first attempt
 * uses the slug picked by `generateUniqueSlug`; each subsequent attempt
 * re-runs the picker (which now sees the conflicting row(s)) and tries
 * the next free `-N` suffix. Five is generous — a real-world burst of
 * five concurrent same-title creates from the same user is implausible.
 */
const SLUG_INSERT_MAX_ATTEMPTS = 5;

/**
 * Insert a watchlist row with a slug that's unique within the user's
 * namespace, retrying on `(user_id, slug)` unique-violation races.
 *
 * Why this exists — issue #3201: the legacy shape was
 *
 *   const slug = await generateUniqueSlug(userId, title); // SELECT
 *   await db.insert(watchlist).values({ slug, ... });     // INSERT
 *
 * a classic TOCTOU: two parallel callers with the same title both
 * observe an empty SELECT and both INSERT the same slug. The
 * `idx_wl_user_slug` UNIQUE index catches the duplicate, but the loser
 * receives an un-handled Postgres `23505` error that propagates out of
 * the server action as a 500.
 *
 * This helper closes the race by treating the unique violation as
 * recoverable: catch it, re-run `generateUniqueSlug` (which now sees
 * the winner's row and picks `-N+1`), and retry the INSERT. The
 * UNIQUE constraint is the source of truth — `generateUniqueSlug` is
 * only an optimisation that picks a sensible starting suffix without
 * spamming the retry loop in the no-contention case.
 *
 * @param userId  authenticated user owning the new watchlist
 * @param title   user-supplied title; used to derive the slug
 * @param insert  callback that runs the actual INSERT given a slug
 *                candidate. Must throw on conflict (the typical drizzle
 *                `.insert().values({ slug, ... }).returning(...)` shape
 *                already does — postgres.js raises the 23505).
 * @returns       the inserted row's data + the final slug. The caller
 *                must trust this slug rather than recomputing it (the
 *                last candidate the loop committed may not match the
 *                first one `generateUniqueSlug` returned).
 */
export async function insertWatchlistWithUniqueSlug<T>(
  userId: string,
  title: string,
  insert: (slug: string) => Promise<T>,
): Promise<{ row: T; slug: string }> {
  let lastErr: unknown;
  for (let attempt = 0; attempt < SLUG_INSERT_MAX_ATTEMPTS; attempt++) {
    // Re-pick the slug on every attempt. On retry, the previous
    // winner's row is now visible to generateUniqueSlug, so the next
    // candidate naturally advances (`my-list` → `my-list-2` → …).
    const slug = await generateUniqueSlug(userId, title);
    try {
      const row = await insert(slug);
      return { row, slug };
    } catch (err) {
      lastErr = err;
      if (isWatchlistSlugUniqueViolation(err)) {
        // Another concurrent caller landed the same slug first. Loop
        // back, re-pick, retry.
        continue;
      }
      // Any other error (network, validation, distinct constraint)
      // propagates immediately so the real failure surfaces.
      throw err;
    }
  }
  // Exhausted the retry budget. Surface the last unique-violation so
  // the caller's existing error handling kicks in instead of returning
  // a silently-wrong row. In practice this branch is unreachable — five
  // concurrent same-title creates from a single user requires sustained
  // contention that no UI surface can produce.
  throw lastErr;
}
