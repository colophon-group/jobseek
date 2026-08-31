import "server-only";

import { eq, and } from "drizzle-orm";
import { db } from "@/db";
import { subscription } from "@/db/schema";
import {
  canCreateWatchlist,
  MAX_WATCHLISTS_PER_ACCOUNT,
} from "@/lib/watchlist-limit";

export type PlanId = "free" | "unlimited";

export interface PlanLimits {
  maxAlerts: number;
  canReceiveAlerts: boolean;
  maxWatchlists: number;
}

export const PLAN_LIMITS: Record<PlanId, PlanLimits> = {
  free: {
    maxAlerts: 0,
    canReceiveAlerts: false,
    maxWatchlists: MAX_WATCHLISTS_PER_ACCOUNT,
  },
  unlimited: {
    maxAlerts: Number.MAX_SAFE_INTEGER,
    canReceiveAlerts: true,
    maxWatchlists: MAX_WATCHLISTS_PER_ACCOUNT,
  },
};

export async function getUserPlan(userId: string): Promise<PlanId> {
  const [row] = await db
    .select({ plan: subscription.plan })
    .from(subscription)
    .where(and(eq(subscription.userId, userId), eq(subscription.status, "active")))
    .limit(1);

  return (row?.plan as PlanId) ?? "free";
}

// Compatibility re-export for existing page-data consumers. The ownership
// ceiling is account-wide domain policy, not a subscription entitlement.
export { canCreateWatchlist };
