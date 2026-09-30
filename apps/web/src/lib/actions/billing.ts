"use server";

import { and, desc, eq } from "drizzle-orm";
import { db } from "@/db";
import { stripeAccount, stripeSubscription, paddleAccount, subscription } from "@/db/schema";
import { getSession, getSessionUserId } from "@/lib/sessionCache";
import { getUserPlan, type PlanId } from "@/lib/plans";
import { stripeCheckoutEnabled, stripeEnvironment, billingOrigin, stripePortalConfigurationId } from "@/lib/stripe/config";
import { checkout, BillingConflict } from "@/lib/stripe/checkout";
import { getStripe } from "@/lib/stripe/client";
import { logExternalError } from "@/lib/safe-external-error";

export type BillingActionErrorCode =
  | "not_authenticated"
  | "payments_unavailable"
  | "billing_account_not_found"
  | "billing_portal_unavailable"
  | "billing_already_subscribed"
  | "billing_processing";

export async function getPlanInfo(): Promise<{
  plan: PlanId;
  checkoutEnabled: boolean;
  hasBillingAccount: boolean;
  trialEligible: boolean;
  status: string | null;
  periodEnd: string | null;
  cancellationScheduled: boolean;
}> {
  const userId = await getSessionUserId();
  const plan = userId ? await getUserPlan(userId) : "free";
  let account;
  let current;
  let trialUsed = false;
  if (userId && process.env.STRIPE_ENVIRONMENT) {
    const [paddle] = await db.select().from(paddleAccount).where(eq(paddleAccount.userId, userId)).limit(1);
    const [legacy] = await db.select().from(subscription).where(eq(subscription.userId, userId)).limit(1);
    trialUsed = Boolean(paddle?.trialUsedAt || legacy?.stripeSubscriptionId);
    [account] = await db.select().from(stripeAccount).where(and(
      eq(stripeAccount.userId, userId), eq(stripeAccount.environment, stripeEnvironment()),
    )).limit(1);
    if (account) {
      [current] = await db.select().from(stripeSubscription)
        .where(eq(stripeSubscription.accountId, account.id))
        .orderBy(desc(stripeSubscription.eventOccurredAt)).limit(1);
    }
  }
  return {
    plan,
    checkoutEnabled: stripeCheckoutEnabled(),
    hasBillingAccount: Boolean(account?.customerId),
    trialEligible: !account?.trialUsedAt && !trialUsed,
    status: current?.status ?? null,
    periodEnd: current?.currentPeriodEnd?.toISOString() ?? null,
    cancellationScheduled: Boolean(current?.scheduledCancelAt),
  };
}

export async function createCheckoutSession(locale = "en", next?: string | null): Promise<{
  url: string | null;
  error?: BillingActionErrorCode;
}> {
  const session = await getSession();
  if (!session?.user) return { url: null, error: "not_authenticated" };
  if (!stripeCheckoutEnabled()) return { url: null, error: "payments_unavailable" };
  try {
    if (await getUserPlan(session.user.id) === "unlimited") return { url: null, error: "billing_already_subscribed" };
    return await checkout(session.user, locale, next);
  } catch (error) {
    if (error instanceof BillingConflict) return { url: null, error: error.code };
    logExternalError("error", { service: "external_http", operation: "stripe.checkout" }, error);
    return { url: null, error: "payments_unavailable" };
  }
}

export async function createPortalSession(locale = "en"): Promise<{
  url: string | null;
  error?: BillingActionErrorCode;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { url: null, error: "not_authenticated" };
  if (!process.env.STRIPE_ENVIRONMENT) return { url: null, error: "billing_portal_unavailable" };
  try {
    const [account] = await db.select().from(stripeAccount).where(and(
      eq(stripeAccount.userId, userId), eq(stripeAccount.environment, stripeEnvironment()),
    )).limit(1);
    if (!account?.customerId) return { url: null, error: "billing_account_not_found" };
    const portal = await getStripe().billingPortal.sessions.create({ customer: account.customerId, configuration: stripePortalConfigurationId(), return_url: new URL(`/${["en", "de", "fr", "it"].includes(locale) ? locale : "en"}/settings/billing`, billingOrigin()).href });
    return { url: portal.url };
  } catch (error) {
    logExternalError("error", { service: "external_http", operation: "stripe.portal" }, error);
    return { url: null, error: "billing_portal_unavailable" };
  }
}
