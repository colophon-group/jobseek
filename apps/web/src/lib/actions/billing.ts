"use server";

import { eq } from "drizzle-orm";
import { db } from "@/db";
import { subscription } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { getUserPlan, PLAN_LIMITS, type PlanId } from "@/lib/plans";

// import Stripe from "stripe";
// const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function getPlanInfo(): Promise<{
  plan: PlanId;
  maxFollowedCompanies: number;
  canReceiveAlerts: boolean;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { plan: "free", ...PLAN_LIMITS.free };

  const plan = await getUserPlan(userId);
  const limits = PLAN_LIMITS[plan];
  return {
    plan,
    maxFollowedCompanies: limits.maxFollowedCompanies,
    canReceiveAlerts: limits.canReceiveAlerts,
  };
}

export async function createCheckoutSession(): Promise<{
  url: string | null;
  error?: string;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { url: null, error: "Not authenticated" };

  // TODO: Uncomment when Stripe is configured
  //
  // const session = await stripe.checkout.sessions.create({
  //   mode: "subscription",
  //   customer_email: user.email, // or use existing stripe customer id
  //   line_items: [{ price: process.env.STRIPE_PRO_PRICE_ID!, quantity: 1 }],
  //   success_url: `${process.env.NEXT_PUBLIC_APP_URL}/app/settings/billing?success=1`,
  //   cancel_url: `${process.env.NEXT_PUBLIC_APP_URL}/app/settings/billing`,
  //   metadata: { userId },
  // });
  //
  // return { url: session.url };

  return { url: null, error: "Payments not yet available" };
}

export async function createPortalSession(): Promise<{
  url: string | null;
  error?: string;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { url: null, error: "Not authenticated" };

  const [sub] = await db
    .select({ stripeCustomerId: subscription.stripeCustomerId })
    .from(subscription)
    .where(eq(subscription.userId, userId))
    .limit(1);

  if (!sub?.stripeCustomerId) {
    return { url: null, error: "No billing account found" };
  }

  // TODO: Uncomment when Stripe is configured
  //
  // const session = await stripe.billingPortal.sessions.create({
  //   customer: sub.stripeCustomerId,
  //   return_url: `${process.env.NEXT_PUBLIC_APP_URL}/app/settings/billing`,
  // });
  //
  // return { url: session.url };

  return { url: null, error: "Billing portal not yet available" };
}
