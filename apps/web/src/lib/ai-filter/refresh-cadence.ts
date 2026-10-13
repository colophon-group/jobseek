import { sql, type SQLWrapper } from "drizzle-orm";

const REPAIR_INTERVAL_MS = 5 * 60_000;

/** Admission and rotating repair must agree on when a saved scope is due. */
export function aiFilterRepairDueCondition(refreshRequestedAt: SQLWrapper, now: Date) {
  return sql`(${refreshRequestedAt} IS NULL OR ${refreshRequestedAt}<${new Date(now.getTime() - REPAIR_INTERVAL_MS).toISOString()}::timestamptz)`;
}
