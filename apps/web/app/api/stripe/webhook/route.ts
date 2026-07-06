import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import Stripe from "stripe";
import { db } from "@/db";
import { subscription } from "@/db/schema";

// Lazily instantiated so the route can boot in environments where Stripe is
// not yet wired (STRIPE_WEBHOOK_SECRET unset). We never construct a real
// Stripe client until we have a secret to verify with.
let _stripe: Stripe | null = null;
function getStripe(): Stripe {
  if (_stripe) return _stripe;
  // The secret key is required only for the Stripe SDK constructor; webhook
  // verification itself uses the webhook signing secret. We pass a non-empty
  // string fallback so the SDK can construct on machines that have the
  // webhook secret but not yet the API key (legitimate during incremental
  // rollout — verification only needs HMAC).
  const apiKey = process.env.STRIPE_SECRET_KEY ?? "sk_placeholder_unused";
  _stripe = new Stripe(apiKey);
  return _stripe;
}

/**
 * Stripe webhook endpoint.
 *
 * Threat model
 * ------------
 * Stripe webhook handlers mutate the `subscription` table on
 * `checkout.session.completed`, `customer.subscription.updated`, and
 * `customer.subscription.deleted` events. Without HMAC verification, any
 * unauthenticated client could POST a forged event and upgrade an arbitrary
 * user to `plan: "unlimited"` or cancel any subscription (#3209).
 *
 * Behavior
 * --------
 * - If `STRIPE_WEBHOOK_SECRET` is unset or empty → return 503 (fail-CLOSED).
 *   We log a single warning per request so the issue is visible in logs but
 *   we do not leak which env var is missing in the response body.
 * - If `STRIPE_WEBHOOK_SECRET` is set → require the `stripe-signature` header
 *   and verify the body with `stripe.webhooks.constructEvent(...)`. A missing
 *   header or a failed verification returns 400.
 *
 * To enable in production:
 * 1. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` env vars.
 * 2. Register this endpoint in the Stripe dashboard.
 */
export async function POST(request: Request) {
  // ── 0. Env-gated kill switch (fail-closed) ─────────────────────────
  const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!webhookSecret) {
    console.warn(
      "[stripe/webhook] STRIPE_WEBHOOK_SECRET is not set — refusing webhook (fail-closed)",
    );
    return NextResponse.json(
      { error: "Stripe webhook endpoint is not configured" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  // ── 1. Verify signature ────────────────────────────────────────────
  // We need the raw text body for HMAC verification — must NOT parse JSON
  // first, since serialization differences would invalidate the signature.
  const body = await request.text();
  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json(
      { error: "Missing stripe-signature header" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  let event: Stripe.Event;
  try {
    event = getStripe().webhooks.constructEvent(body, signature, webhookSecret);
  } catch (err) {
    // Stripe raises `StripeSignatureVerificationError` for bad signatures;
    // other parse failures also land here. Either way the request is
    // untrusted — log enough to debug, return a generic 400.
    const message = err instanceof Error ? err.message : "unknown error";
    console.warn("[stripe/webhook] signature verification failed", {
      reason: message,
    });
    return NextResponse.json(
      { error: "Invalid signature" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  // ── 2. Handle events ─────────────────────────────────────────────
  // The cast goes through `unknown` because the Stripe.Event union is wider
  // than `Record<string, unknown>` (some members are nominal classes). The
  // switch below narrows on `event.type` and only reads fields that exist
  // on the relevant payload variant.
  const obj = event.data.object as unknown as Record<string, unknown>;

  switch (event.type) {
    case "checkout.session.completed": {
      const userId = (obj.metadata as Record<string, string> | undefined)?.userId;
      const customerId = obj.customer as string | undefined;
      const subscriptionId = obj.subscription as string | undefined;
      if (userId && customerId && subscriptionId) {
        await activateSubscription(userId, customerId, subscriptionId);
      }
      break;
    }

    case "customer.subscription.updated": {
      const subscriptionId = obj.id as string | undefined;
      const status = obj.status as string | undefined;
      if (subscriptionId && status) {
        await updateSubscriptionStatus(subscriptionId, status);
      }
      break;
    }

    case "customer.subscription.deleted": {
      const subscriptionId = obj.id as string | undefined;
      if (subscriptionId) {
        await cancelSubscription(subscriptionId);
      }
      break;
    }

    case "invoice.payment_failed": {
      // Future: notify user, flag account
      break;
    }
  }

  return NextResponse.json({ received: true });
}

// ── DB helpers ────────────────────────────────────────────────────

async function activateSubscription(
  userId: string,
  stripeCustomerId: string,
  stripeSubscriptionId: string,
) {
  const [existing] = await db
    .select({ id: subscription.id })
    .from(subscription)
    .where(eq(subscription.userId, userId))
    .limit(1);

  if (existing) {
    await db
      .update(subscription)
      .set({
        plan: "unlimited",
        status: "active",
        stripeCustomerId,
        stripeSubscriptionId,
        updatedAt: new Date(),
      })
      .where(eq(subscription.id, existing.id));
  } else {
    await db.insert(subscription).values({
      userId,
      plan: "unlimited",
      status: "active",
      stripeCustomerId,
      stripeSubscriptionId,
      startsAt: new Date(),
    });
  }
}

async function updateSubscriptionStatus(
  stripeSubscriptionId: string,
  stripeStatus: string,
) {
  // Map Stripe status to our enum
  const statusMap: Record<string, "active" | "cancelled" | "expired"> = {
    active: "active",
    past_due: "active", // grace period — keep access
    canceled: "cancelled",
    unpaid: "expired",
  };
  const status = statusMap[stripeStatus] ?? "active";

  await db
    .update(subscription)
    .set({ status, updatedAt: new Date() })
    .where(eq(subscription.stripeSubscriptionId, stripeSubscriptionId));
}

async function cancelSubscription(stripeSubscriptionId: string) {
  await db
    .update(subscription)
    .set({
      status: "cancelled",
      endsAt: new Date(),
      updatedAt: new Date(),
    })
    .where(eq(subscription.stripeSubscriptionId, stripeSubscriptionId));
}
