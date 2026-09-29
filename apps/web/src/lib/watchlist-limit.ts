import "server-only";

import { count, eq, sql } from "drizzle-orm";
import { db } from "@/db";
import { watchlist } from "@/db/schema";

export const MAX_WATCHLISTS_PER_ACCOUNT = 10;

/** Server-managed exceptions use stable account IDs, never client input or plan. */
function watchlistLimitForAccount(userId: string): number | null {
  const exemptIds = (process.env.WATCHLIST_LIMIT_EXEMPT_USER_IDS ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
  return userId && exemptIds.includes(userId) ? null : MAX_WATCHLISTS_PER_ACCOUNT;
}

type WatchlistTransaction = Parameters<
  Parameters<typeof db.transaction>[0]
>[0];

export class WatchlistLimitReachedError extends Error {
  constructor() {
    super("Watchlist ownership limit reached");
    this.name = "WatchlistLimitReachedError";
  }
}

/**
 * Run a watchlist-creating mutation while holding the account's creation
 * slot. The transaction-scoped advisory lock serializes all inserts for one
 * owner, so the count and insert cannot race with another create/copy/handoff
 * request. Callers must keep the callback and this helper in the same
 * READ COMMITTED transaction.
 */
export async function createWithinWatchlistLimit<T>(
  tx: WatchlistTransaction,
  userId: string,
  create: () => Promise<T>,
): Promise<T> {
  // hashtextextended gives the UUID/string account id a stable 64-bit lock
  // key. The issue number is a namespace seed, preventing accidental reuse
  // by unrelated account-scoped advisory locks.
  await tx.execute(sql`
    SELECT pg_advisory_xact_lock(
      hashtextextended(${userId}, 8316::bigint)
    )
  `);

  const [{ value: current }] = await tx
    .select({ value: count() })
    .from(watchlist)
    .where(eq(watchlist.userId, userId));

  const max = watchlistLimitForAccount(userId);
  if (max !== null && current >= max) {
    throw new WatchlistLimitReachedError();
  }

  return create();
}

export async function canCreateWatchlist(
  userId: string,
): Promise<{ allowed: boolean; current: number; max: number | null }> {
  const [{ value: current }] = await db
    .select({ value: count() })
    .from(watchlist)
    .where(eq(watchlist.userId, userId));

  const max = watchlistLimitForAccount(userId);
  return {
    allowed: max === null || current < max,
    current,
    max,
  };
}
