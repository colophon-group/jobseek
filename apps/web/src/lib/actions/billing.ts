"use server";

import { and, desc, eq, ne } from "drizzle-orm";
import { db } from "@/db";
import { paddleAccount, paddleSubscription } from "@/db/schema";
import { getSession, getSessionUserId } from "@/lib/sessionCache";
import { getUserPlan, type PlanId } from "@/lib/plans";
import { paddleCheckoutEnabled, paddleEnvironment, paddlePriceIds } from "@/lib/paddle/config";
import { getPaddle } from "@/lib/paddle/client";
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
  if (userId && process.env.PADDLE_ENVIRONMENT) {
    [account] = await db.select().from(paddleAccount).where(and(
      eq(paddleAccount.userId, userId), eq(paddleAccount.environment, paddleEnvironment()),
    )).limit(1);
    if (account) {
      [current] = await db.select().from(paddleSubscription)
        .where(eq(paddleSubscription.accountId, account.id))
        .orderBy(desc(paddleSubscription.eventOccurredAt)).limit(1);
    }
  }
  return {
    plan,
    checkoutEnabled: paddleCheckoutEnabled(),
    hasBillingAccount: Boolean(account?.customerId),
    trialEligible: !account?.trialUsedAt,
    status: current?.status ?? null,
    periodEnd: current?.currentPeriodEnd?.toISOString() ?? null,
    cancellationScheduled: Boolean(current?.scheduledCancelAt),
  };
}

export async function createCheckoutSession(): Promise<{
  transactionId: string | null;
  email?: string;
  error?: BillingActionErrorCode;
}> {
  const session = await getSession();
  if (!session?.user) return { transactionId: null, error: "not_authenticated" };
  if (!paddleCheckoutEnabled()) return { transactionId: null, error: "payments_unavailable" };
  const userId = session.user.id;
  try {
    if (await getUserPlan(userId) === "unlimited") {
      return { transactionId: null, error: "billing_already_subscribed" };
    }
    const paddle = getPaddle();
    const environment = paddleEnvironment();
    const prices = paddlePriceIds();
    return await db.transaction(async (tx) => {
      await tx.insert(paddleAccount).values({ userId, environment }).onConflictDoNothing();
      const [account] = await tx.select().from(paddleAccount).where(and(
        eq(paddleAccount.userId, userId), eq(paddleAccount.environment, environment),
      )).for("update");
      if (account.deletionRequested) return { transactionId: null, error: "payments_unavailable" as const };
      const [existing] = await tx.select({ id: paddleSubscription.id }).from(paddleSubscription)
        .where(and(eq(paddleSubscription.accountId, account.id), ne(paddleSubscription.status, "canceled")))
        .limit(1);
      if (existing) return { transactionId: null, error: "billing_already_subscribed" as const };

      // Reuse across double-clicks/tabs; never create another transaction while
      // a completed checkout is waiting for its provisioning webhook.
      if (account.pendingTransactionId) {
        const pending = await paddle.transactions.get(account.pendingTransactionId);
        if (pending.status === "draft" || pending.status === "ready") {
          return { transactionId: pending.id, email: session.user.email };
        }
        if (pending.status !== "canceled") {
          return { transactionId: null, error: "billing_processing" as const };
        }
      }
      const priceId = account.trialUsedAt ? prices.returning : prices.trial;
      const transaction = await paddle.transactions.create({
        items: [{ priceId, quantity: 1 }],
        collectionMode: "automatic",
        ...(account.customerId ? { customerId: account.customerId } : {}),
        customData: { jobseek_account_id: account.id },
      });
      await tx.update(paddleAccount).set({
        pendingTransactionId: transaction.id, pendingPriceId: priceId,
      }).where(eq(paddleAccount.id, account.id));
      return { transactionId: transaction.id, email: session.user.email };
    });
  } catch (error) {
    logExternalError("error", { service: "external_http", operation: "paddle.checkout" }, error);
    return { transactionId: null, error: "payments_unavailable" };
  }
}

export async function createPortalSession(): Promise<{
  url: string | null;
  error?: BillingActionErrorCode;
}> {
  const userId = await getSessionUserId();
  if (!userId) return { url: null, error: "not_authenticated" };
  if (!process.env.PADDLE_ENVIRONMENT) return { url: null, error: "billing_portal_unavailable" };
  try {
    const [account] = await db.select().from(paddleAccount).where(and(
      eq(paddleAccount.userId, userId), eq(paddleAccount.environment, paddleEnvironment()),
    )).limit(1);
    if (!account?.customerId) return { url: null, error: "billing_account_not_found" };
    const portal = await getPaddle().customerPortalSessions.create(account.customerId, []);
    return { url: portal.urls.general.overview };
  } catch (error) {
    logExternalError("error", { service: "external_http", operation: "paddle.portal" }, error);
    return { url: null, error: "billing_portal_unavailable" };
  }
}
