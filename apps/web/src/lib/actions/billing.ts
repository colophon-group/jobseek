"use server";

import { eq } from "drizzle-orm";
import { db } from "@/db";
import { subscription } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { getUserPlan, PLAN_LIMITS, type PlanId } from "@/lib/plans";

// import Stripe from "stripe";
// const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export type BillingActionErrorCode =
  | "not_authenticated"
  | "payments_unavailable"
  | "billing_account_not_found"
  | "billing_portal_unavailable";

export async function getPlanInfo(): Promise<{
  plan: PlanId;
  canReceiveAlerts: boolean;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { plan: "free", canReceiveAlerts: PLAN_LIMITS.free.canReceiveAlerts };

  const plan = await getUserPlan(userId);
  const limits = PLAN_LIMITS[plan];
  return {
    plan,
    canReceiveAlerts: limits.canReceiveAlerts,
  };
}

export async function createCheckoutSession(): Promise<{
  url: string | null;
  error?: BillingActionErrorCode;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { url: null, error: "not_authenticated" };

  // TODO: Uncomment when Stripe is configured
  //
  // const session = await stripe.checkout.sessions.create({
  //   mode: "subscription",
  //   customer_email: user.email, // or use existing stripe customer id
  //   line_items: [{ price: process.env.STRIPE_PRO_PRICE_ID!, quantity: 1 }],
  //   success_url: `${process.env.NEXT_PUBLIC_APP_URL}/settings/billing?success=1`,
  //   cancel_url: `${process.env.NEXT_PUBLIC_APP_URL}/settings/billing`,
  //   metadata: { userId },
  // });
  //
  // return { url: session.url };

  return { url: null, error: "payments_unavailable" };
}

export async function createPortalSession(): Promise<{
  url: string | null;
  error?: BillingActionErrorCode;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { url: null, error: "not_authenticated" };

  const [sub] = await db
    .select({ stripeCustomerId: subscription.stripeCustomerId })
    .from(subscription)
    .where(eq(subscription.userId, userId))
    .limit(1);

  if (!sub?.stripeCustomerId) {
    return { url: null, error: "billing_account_not_found" };
  }

  // TODO: Uncomment when Stripe is configured
  //
  // const session = await stripe.billingPortal.sessions.create({
  //   customer: sub.stripeCustomerId,
  //   return_url: `${process.env.NEXT_PUBLIC_APP_URL}/settings/billing`,
  // });
  //
  // return { url: session.url };

  return { url: null, error: "billing_portal_unavailable" };
}
