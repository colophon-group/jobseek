import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { timingSafeEqual } from "crypto";
import { db } from "@/db";
import { subscription } from "@/db/schema";

/**
 * Stripe webhook endpoint.
 *
 * Signature verification uses the raw request body and the stripe-signature
 * header against STRIPE_WEBHOOK_SECRET.  Events are rejected with 400 if the
 * signature is missing or does not match — this prevents anyone from forging
 * subscription-activation events.
 *
 * Required env vars:
 *   STRIPE_WEBHOOK_SECRET  — whsec_… value from the Stripe dashboard
 */

/** Maximum age of a Stripe webhook event (±5 minutes). */
const STRIPE_TIMESTAMP_TOLERANCE_SECONDS = 300;

/** Verify a Stripe webhook signature without the stripe SDK. */
async function verifyStripeSignature(
  body: string,
  sigHeader: string,
  secret: string,
): Promise<boolean> {
  try {
    // sigHeader format: "t=<timestamp>,v1=<sig1>,v1=<sig2>,..."
    const parts = sigHeader.split(",");
    const tPart = parts.find((p) => p.startsWith("t="));
    const v1Parts = parts.filter((p) => p.startsWith("v1="));
    if (!tPart || v1Parts.length === 0) return false;

    const timestamp = tPart.slice(2);

    // Replay-attack protection: reject events older (or newer) than 5 minutes
    const eventTimeSec = parseInt(timestamp, 10);
    if (!isFinite(eventTimeSec)) return false;
    const nowSec = Math.floor(Date.now() / 1000);
    if (Math.abs(nowSec - eventTimeSec) > STRIPE_TIMESTAMP_TOLERANCE_SECONDS) {
      return false;
    }

    const signed_payload = `${timestamp}.${body}`;

    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw",
      enc.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(signed_payload));
    const computed = Buffer.from(sig).toString("hex");

    // Check that at least one v1 signature matches (timing-safe)
    return v1Parts.some((p) => {
      const provided = p.slice(3);
      if (provided.length !== computed.length) return false;
      try {
        return timingSafeEqual(Buffer.from(provided), Buffer.from(computed));
      } catch {
        return false;
      }
    });
  } catch {
    return false;
  }
}

export async function POST(request: Request) {
  const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!webhookSecret) {
    // Webhook secret not configured — refuse all events rather than
    // falling back to unverified processing.
    console.error("STRIPE_WEBHOOK_SECRET is not set; rejecting webhook");
    return NextResponse.json(
      { error: "Webhook not configured" },
      { status: 503 },
    );
  }

  // ── 1. Verify signature ──────────────────────────────────────────
  const body = await request.text();
  const sig = request.headers.get("stripe-signature");
  if (!sig) {
    return NextResponse.json({ error: "Missing stripe-signature header" }, { status: 400 });
  }

  const valid = await verifyStripeSignature(body, sig, webhookSecret);
  if (!valid) {
    return NextResponse.json({ error: "Invalid signature" }, { status: 400 });
  }

  // ── 2. Parse verified body ────────────────────────────────────────
  let event: { type: string; data: { object: Record<string, unknown> } };
  try {
    event = JSON.parse(body);
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
