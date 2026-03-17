import "server-only";

import { eq, and, count } from "drizzle-orm";
import { db } from "@/db";
import { subscription, followedCompany } from "@/db/schema";

export type PlanId = "free" | "unlimited";

export interface PlanLimits {
  maxFollowedCompanies: number;
  maxAlerts: number;
  canReceiveAlerts: boolean;
}

export const PLAN_LIMITS: Record<PlanId, PlanLimits> = {
  free: {
    maxFollowedCompanies: 5,
    maxAlerts: 0,
    canReceiveAlerts: false,
  },
  unlimited: {
    maxFollowedCompanies: Number.MAX_SAFE_INTEGER,
    maxAlerts: Number.MAX_SAFE_INTEGER,
    canReceiveAlerts: true,
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

export async function canFollowMore(
  userId: string,
): Promise<{ allowed: boolean; current: number; max: number }> {
  const plan = await getUserPlan(userId);
  const limits = PLAN_LIMITS[plan];

  const [{ value: current }] = await db
    .select({ value: count() })
    .from(followedCompany)
    .where(eq(followedCompany.userId, userId));

  return {
    allowed: current < limits.maxFollowedCompanies,
    current,
    max: limits.maxFollowedCompanies,
  };
}
