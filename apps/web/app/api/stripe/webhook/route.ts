import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { subscription } from "@/db/schema";

// import Stripe from "stripe";
// const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

/**
 * Stripe webhook endpoint.
 *
 * To enable:
 * 1. Install stripe: pnpm add stripe
 * 2. Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET env vars
 * 3. Uncomment the Stripe import and verification code below
 */
export async function POST(request: Request) {
  // ── 1. Verify signature ──────────────────────────────────────────
  // const body = await request.text();
  // const sig = request.headers.get("stripe-signature")!;
  // let event: Stripe.Event;
  // try {
  //   event = stripe.webhooks.constructEvent(
  //     body,
  //     sig,
  //     process.env.STRIPE_WEBHOOK_SECRET!,
  //   );
  // } catch {
  //   return NextResponse.json({ error: "Invalid signature" }, { status: 400 });
  // }

  // ── Stub: parse JSON directly (remove when Stripe is wired) ─────
  let event: { type: string; data: { object: Record<string, unknown> } };
  try {
    event = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  // ── 2. Handle events ─────────────────────────────────────────────
  const obj = event.data.object;

  switch (event.type) {
    case "checkout.session.completed": {
      const userId = (obj.metadata as Record<string, string>)?.userId;
      const customerId = obj.customer as string;
      const subscriptionId = obj.subscription as string;
      if (userId && customerId && subscriptionId) {
        await activateSubscription(userId, customerId, subscriptionId);
      }
      break;
    }

    case "customer.subscription.updated": {
      const subscriptionId = obj.id as string;
      const status = obj.status as string;
      if (subscriptionId) {
        await updateSubscriptionStatus(subscriptionId, status);
      }
      break;
    }

    case "customer.subscription.deleted": {
      const subscriptionId = obj.id as string;
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
