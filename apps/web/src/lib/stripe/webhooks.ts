import "server-only";
import { and, eq } from "drizzle-orm";
import type Stripe from "stripe";
import { db } from "@/db";
import { stripeAccount, stripeSubscription } from "@/db/schema";
import { getStripe } from "./client";
import { stripeEnvironment } from "./config";
import { stripeId, subscriptionState } from "./state";

export const STRIPE_EVENTS = new Set([
  "checkout.session.completed", "checkout.session.async_payment_succeeded",
  "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
  "customer.subscription.paused", "customer.subscription.resumed",
  "invoice.paid", "invoice.payment_failed", "invoice.payment_action_required",
]);

export async function applyStripeEvent(event: Stripe.Event) {
  const environment = stripeEnvironment();
  if (event.livemode !== (environment === "production")) throw new Error("Webhook environment mismatch");
  if (!STRIPE_EVENTS.has(event.type)) return;
  const stripe = getStripe();
  const object = event.data.object;
  let id: string | null = null;
  if (object.object === "subscription") id = object.id;
  if (object.object === "checkout.session") id = stripeId(object.subscription);
  if (object.object === "invoice") id = stripeId(object.parent?.subscription_details?.subscription ?? null);
  if (!id || !("customer" in object)) return;
  const customerId = stripeId(object.customer);
  if (!customerId) return;
  await db.transaction(async tx => {
    const [account] = await tx.select().from(stripeAccount).where(and(eq(stripeAccount.customerId, customerId), eq(stripeAccount.environment, environment))).for("update");
    if (!account) return;
    const [existing] = await tx.select().from(stripeSubscription).where(eq(stripeSubscription.id, id!)).limit(1);
    let expectedPriceId = existing?.expectedPriceId;
    if (existing && existing.accountId !== account.id) throw new Error("Subscription account mismatch");
    if (!existing) {
      let checkout: Stripe.Checkout.Session | undefined = account.pendingCheckoutId ? await stripe.checkout.sessions.retrieve(account.pendingCheckoutId) : undefined;
      if (!checkout && account.checkoutParameters) {
        for await (const session of stripe.checkout.sessions.list({ customer: customerId, limit: 100 })) {
          if (session.metadata?.jobseek_attempt_id === account.checkoutAttemptId) { checkout = session; break; }
        }
      }
      // Metadata alone cannot bind a subscription. Match the authenticated,
      // server-created Checkout request, customer and exact subscription.
      if (!checkout || checkout.status !== "complete") {
        if (account.checkoutParameters) throw new Error("Awaiting completed Checkout");
        return;
      }
      if (stripeId(checkout.subscription) !== id || stripeId(checkout.customer) !== customerId || checkout.mode !== "subscription" ||
          checkout.client_reference_id !== account.id || checkout.livemode !== event.livemode || !account.pendingPriceId) return;
      expectedPriceId = account.pendingPriceId;
    }
    // Read AFTER the account lock. Timestamp ties, duplicate events and old
    // snapshots all converge to the provider's present state.
    const current = await stripe.subscriptions.retrieve(id!);
    if (stripeId(current.customer) !== customerId || current.livemode !== event.livemode) throw new Error("Subscription customer/environment mismatch");
    const state = subscriptionState(current, expectedPriceId!, event.livemode);
    if (account.deletionRequested) state.entitled = false;
    const values = { accountId: account.id, expectedPriceId: expectedPriceId!, ...state, eventOccurredAt: new Date().toISOString() };
    await tx.insert(stripeSubscription).values({ id: current.id, ...values }).onConflictDoUpdate({ target: stripeSubscription.id, set: { ...values, updatedAt: new Date() } });
    if (!existing) await tx.update(stripeAccount).set({ pendingCheckoutId: null, pendingPriceId: null, checkoutParameters: null, trialUsedAt: account.trialUsedAt ?? new Date(), checkoutAttemptId: crypto.randomUUID() }).where(eq(stripeAccount.id, account.id));
  });
}
