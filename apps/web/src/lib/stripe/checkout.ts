import "server-only";
import { randomUUID } from "node:crypto";
import { and, eq } from "drizzle-orm";
import type Stripe from "stripe";
import { db } from "@/db";
import { stripeAccount, paddleAccount, subscription } from "@/db/schema";
import { normalizeAuthReturnPath } from "@/lib/auth-return";
import { checkoutPolicyMessage } from "./checkout-policy";
import { getStripe } from "./client";
import { billingOrigin, stripeEnvironment, stripePriceId } from "./config";

export class BillingConflict extends Error {
  constructor(public code: "billing_already_subscribed" | "billing_processing" | "payments_unavailable") { super(code); }
}

/** Persist customer and request parameters before creating a payable session.
 * A failed DB commit after the API call can then recover the identical session. */
export async function checkout(user: { id: string; email: string }, locale = "en", next?: string | null): Promise<{ url: string | null; error?: "billing_processing" }> {
  const stripe = getStripe();
  const environment = stripeEnvironment();
  const priceId = stripePriceId();
  const price = await stripe.prices.retrieve(priceId);
  if (!price.active || price.livemode !== (environment === "production") || price.currency !== "usd" ||
      price.unit_amount !== 1000 || price.recurring?.interval !== "month" || price.recurring.interval_count !== 1 ||
      price.type !== "recurring" || price.billing_scheme !== "per_unit" || price.tax_behavior !== "exclusive") {
    throw new Error("Stripe Pro price must be monthly USD 10, exclusive tax, in the matching environment");
  }
  const lang = (["en", "de", "fr", "it"].includes(locale) ? locale : "en") as "en" | "de" | "fr" | "it";
  const policyMessage = await checkoutPolicyMessage(lang);
  const destination = new URL(`/${lang}/settings/billing`, billingOrigin());
  const returnPath = normalizeAuthReturnPath(next);
  if (returnPath) destination.searchParams.set("next", returnPath);
  const cancelUrl = destination.href;
  destination.searchParams.set("checkout", "complete");

  await db.transaction(async tx => {
    await tx.insert(stripeAccount).values({ userId: user.id, environment }).onConflictDoNothing();
    const [account] = await tx.select().from(stripeAccount).where(and(eq(stripeAccount.userId, user.id), eq(stripeAccount.environment, environment))).for("update");
    if (account.deletionRequested) throw new BillingConflict("payments_unavailable");
    if (!account.customerId) {
      const customer = await stripe.customers.create({ email: user.email, metadata: { jobseek_account_id: account.id } }, { idempotencyKey: `jobseek-customer:${account.id}` });
      await tx.update(stripeAccount).set({ customerId: customer.id }).where(eq(stripeAccount.id, account.id));
    }
  });

  // Commit the exact request and stable idempotency token independently of the
  // external session call, including the trial decision and return destination.
  await db.transaction(async tx => {
    const [account] = await tx.select().from(stripeAccount).where(and(eq(stripeAccount.userId, user.id), eq(stripeAccount.environment, environment))).for("update");
    if (account.deletionRequested) throw new BillingConflict("payments_unavailable");
    if (account.checkoutParameters || account.pendingCheckoutId) return;
    const [legacy] = await tx.select().from(paddleAccount).where(eq(paddleAccount.userId, user.id)).limit(1);
    const [historical] = await tx.select().from(subscription).where(eq(subscription.userId, user.id)).limit(1);
    const subscriptions = await stripe.subscriptions.list({ customer: account.customerId!, status: "all", limit: 1 });
    const trialUsed = Boolean(account.trialUsedAt || legacy?.trialUsedAt || historical?.stripeSubscriptionId || subscriptions.data.length);
    await tx.update(stripeAccount).set({
      pendingPriceId: priceId,
      ...(trialUsed && !account.trialUsedAt ? { trialUsedAt: new Date() } : {}),
      checkoutParameters: {
        mode: "subscription", managed_payments: { enabled: false }, customer: account.customerId!, client_reference_id: account.id,
        line_items: [{ price: priceId, quantity: 1 }], payment_method_collection: "always",
        automatic_tax: { enabled: true }, billing_address_collection: "required",
        customer_update: { address: "auto", name: "auto" },
        subscription_data: { billing_mode: { type: "flexible" }, metadata: { jobseek_account_id: account.id },
          ...(!trialUsed ? { trial_period_days: 7, trial_settings: { end_behavior: { missing_payment_method: "cancel" } } } : {}) },
        metadata: { jobseek_attempt_id: account.checkoutAttemptId },
        success_url: destination.href, cancel_url: cancelUrl, locale: lang as Stripe.Checkout.SessionCreateParams.Locale,
        consent_collection: { terms_of_service: "required" },
        custom_text: { terms_of_service_acceptance: { message: policyMessage } },
        expires_at: Math.floor(Date.now() / 1000) + 23 * 3600,
      },
    }).where(eq(stripeAccount.id, account.id));
  });

  const result = await db.transaction(async tx => {
    const [account] = await tx.select().from(stripeAccount).where(and(eq(stripeAccount.userId, user.id), eq(stripeAccount.environment, environment))).for("update");
    if (account.deletionRequested) throw new BillingConflict("payments_unavailable");
    // Recovery also works after Stripe's 24h idempotency retention. Customer
    // and request are durable before any payable session exists.
    let pending: Stripe.Checkout.Session | undefined = account.pendingCheckoutId ? await stripe.checkout.sessions.retrieve(account.pendingCheckoutId) : undefined;
    if (!pending) {
      for await (const session of stripe.checkout.sessions.list({ customer: account.customerId!, limit: 100 })) {
        if (session.metadata?.jobseek_attempt_id === account.checkoutAttemptId) { pending = session; break; }
      }
    }
    if (pending) {
      if (pending.livemode !== (environment === "production") || pending.customer !== account.customerId) throw new Error("Checkout environment/customer mismatch");
      if (pending.status === "complete") return { url: null, error: "billing_processing" as const };
      if (pending.status === "open") {
        await tx.update(stripeAccount).set({ pendingCheckoutId: pending.id }).where(eq(stripeAccount.id, account.id));
        return { url: pending.url };
      }
    }
    if (pending?.status === "expired" || (account.checkoutParameters?.expires_at ?? 0) <= Math.floor(Date.now() / 1000) + 1800) {
      await tx.update(stripeAccount).set({ pendingCheckoutId: null, pendingPriceId: null, checkoutParameters: null, checkoutAttemptId: randomUUID() }).where(eq(stripeAccount.id, account.id));
      return { url: null, retry: true as const };
    }
    for await (const subscription of stripe.subscriptions.list({ customer: account.customerId!, status: "all", limit: 100 })) {
      if (!["canceled", "incomplete_expired"].includes(subscription.status)) throw new BillingConflict("billing_already_subscribed");
    }
    const session = await stripe.checkout.sessions.create(account.checkoutParameters!, { idempotencyKey: `jobseek-checkout:${account.checkoutAttemptId}` });
    await tx.update(stripeAccount).set({ pendingCheckoutId: session.id }).where(eq(stripeAccount.id, account.id));
    return { url: session.url };
  });
  if ("retry" in result) return checkout(user, lang, next);
  return result;
}
