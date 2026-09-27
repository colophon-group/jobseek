import "server-only";

import { sql, type SQLWrapper } from "drizzle-orm";
import type { db } from "@/db";
import { paddleEnvironment } from "@/lib/paddle/config";

/** One paid-access rule for page bootstrap and AI execution. */
export function paidEntitlementCondition(userId: string | SQLWrapper, now = new Date()) {
  const manual = sql`EXISTS (
    SELECT 1 FROM subscription entitlement_manual
    WHERE entitlement_manual.user_id = ${userId}
      AND entitlement_manual.plan = 'unlimited' AND entitlement_manual.status = 'active'
      AND (entitlement_manual.ends_at IS NULL OR entitlement_manual.ends_at > ${now.toISOString()}::timestamptz)
  )`;
  // Allows rollout before Paddle is configured/migrated. Never query a sandbox
  // account's entitlements when the application is configured for production.
  if (!process.env.PADDLE_ENVIRONMENT) return manual;
  return sql`(${manual} OR EXISTS (
    SELECT 1 FROM paddle_subscription entitlement_sub
    JOIN paddle_account entitlement_account ON entitlement_account.id = entitlement_sub.account_id
    WHERE entitlement_account.user_id = ${userId}
      AND entitlement_account.environment = ${paddleEnvironment()}
      AND entitlement_sub.entitled = true
      AND entitlement_sub.status IN ('active', 'trialing')
      AND entitlement_sub.current_period_end > ${now.toISOString()}::timestamptz
  ))`;
}

export async function hasPaidEntitlement(queryable: Pick<typeof db, "execute">, userId: string, now = new Date()) {
  const [row] = await queryable.execute<{ entitled: boolean }>(
    sql`SELECT ${paidEntitlementCondition(userId, now)} AS entitled`,
  );
  return row?.entitled === true;
}
