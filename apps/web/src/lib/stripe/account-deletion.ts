import "server-only";
import { and, eq } from "drizzle-orm";
import { db } from "@/db";
import { stripeAccount, stripeSubscription, subscription } from "@/db/schema";
import { getStripe } from "./client";
import { stripeEnvironment } from "./config";
import { stripeId } from "./state";

export async function prepareStripeAccountDeletion(userId: string) {
  const [legacy] = await db.select().from(subscription).where(eq(subscription.userId, userId)).limit(1);
  if (legacy?.stripeSubscriptionId) throw new Error("Historical Stripe billing must be reconciled before deletion");
  if (!process.env.STRIPE_ENVIRONMENT) return;
  const environment = stripeEnvironment();
  const accounts = await db.select().from(stripeAccount).where(eq(stripeAccount.userId, userId));
  if (accounts.some(a => a.environment !== environment && a.customerId)) throw new Error("Cancel billing in the other environment before deletion");
  const account = await db.transaction(async tx => {
    await tx.insert(stripeAccount).values({ userId, environment }).onConflictDoNothing();
    const [row] = await tx.select().from(stripeAccount).where(and(eq(stripeAccount.userId, userId), eq(stripeAccount.environment, environment))).for("update");
    await tx.update(stripeAccount).set({ deletionRequested: true }).where(eq(stripeAccount.id, row.id));
    await tx.update(stripeSubscription).set({ entitled: false }).where(eq(stripeSubscription.accountId, row.id));
    return row;
  });
  if (!account.customerId) return;
  const stripe = getStripe();
  // Enumerate provider objects, including creations whose DB commit failed.
  // Checkout holds the same account lock, so deletion intent waits for it.
  const ids = new Set<string>();
  for await (const checkout of stripe.checkout.sessions.list({ customer: account.customerId, limit: 100 })) {
    if (checkout.status === "open") await stripe.checkout.sessions.expire(checkout.id);
    if (checkout.status === "complete") {
      const id = stripeId(checkout.subscription);
      if (!id) throw new Error("Awaiting checkout provisioning before deletion");
      ids.add(id);
    }
  }
  for await (const current of stripe.subscriptions.list({ customer: account.customerId, status: "all", limit: 100 })) {
    if (!["canceled", "incomplete_expired"].includes(current.status)) ids.add(current.id);
  }
  for (const id of ids) {
    const current = await stripe.subscriptions.retrieve(id);
    if (!["canceled", "incomplete_expired"].includes(current.status)) await stripe.subscriptions.cancel(id, { prorate: false, invoice_now: false });
  }
}
