import "server-only";

import { db } from "@/db";
import { hasPaidEntitlement } from "@/lib/paid-entitlement";
import {
  canCreateWatchlist,
  MAX_WATCHLISTS_PER_ACCOUNT,
} from "@/lib/watchlist-limit";

export type PlanId = "free" | "unlimited";

export interface PlanLimits {
  maxWatchlists: number;
}

export const PLAN_LIMITS: Record<PlanId, PlanLimits> = {
  free: {
    maxWatchlists: MAX_WATCHLISTS_PER_ACCOUNT,
  },
  unlimited: {
    maxWatchlists: MAX_WATCHLISTS_PER_ACCOUNT,
  },
};

export async function getUserPlan(userId: string): Promise<PlanId> {
  return await hasPaidEntitlement(db, userId) ? "unlimited" : "free";
}

// Compatibility re-export for existing page-data consumers. The ownership
// ceiling is account-wide domain policy, not a subscription entitlement.
export { canCreateWatchlist };
