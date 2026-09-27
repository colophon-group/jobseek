import "server-only";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { NOTIFICATION_DELIVERY_RETENTION_DAYS } from "@/lib/notifications/policy";

/** Unresolved rows retain their idempotency barrier indefinitely. */
export async function cleanupNotificationRetention() {
  const before = new Date(Date.now() - NOTIFICATION_DELIVERY_RETENTION_DAYS * 86400000).toISOString();
  await db.execute(sql`
    DELETE FROM notification_delivery WHERE id IN (
      SELECT id FROM notification_delivery
      WHERE status IN ('sent', 'skipped') AND completed_at < ${before}::timestamptz
      ORDER BY completed_at LIMIT 1000
    )
  `);
  // Keep quota buckets as long as completed deliveries. They contain counts,
  // not recipients, and expired months cannot authorize a present-day send.
  await db.execute(sql`DELETE FROM notification_quota
    WHERE (period LIKE 'day:%' AND period < ${`day:${before.slice(0, 10)}`})
       OR (period LIKE 'month:%' AND period < ${`month:${before.slice(0, 7)}`})`);
}
