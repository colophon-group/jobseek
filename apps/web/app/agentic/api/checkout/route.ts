/**
 * GET /agentic/api/checkout?userId=&successUrl=&cancelUrl=
 *
 * Creates a Stripe Checkout session for the given user.
 * No auth required — the agent builds this URL and opens it for the user.
 *
 * Returns: { checkoutUrl: string }
 */
import { type NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { user } from "@/db/schema";

const PORTAL_URL = process.env.NEXT_PUBLIC_PORTAL_URL ?? `${process.env.NEXT_PUBLIC_SITE_URL ?? "https://jseek.co"}/agentic`;

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;
  const userId = searchParams.get("userId");
  const successUrl = searchParams.get("successUrl") ?? `${PORTAL_URL}?subscribed=1`;
  const cancelUrl = searchParams.get("cancelUrl") ?? PORTAL_URL;

  if (!userId) {
    return NextResponse.json({ error: "userId is required" }, { status: 400 });
  }

  const stripeKey = process.env.STRIPE_SECRET_KEY;
  const priceId = process.env.STRIPE_PRICE_ID;
  if (!stripeKey || !priceId) {
    return NextResponse.json(
      { error: "Payments not configured on this server" },
      { status: 503 },
    );
  }

  // Resolve email for Stripe
  const rows = await db
    .select({ email: user.email })
    .from(user)
    .where(eq(user.id, userId))
    .limit(1);

  if (!rows.length) {
    return NextResponse.json({ error: "User not found" }, { status: 404 });
  }

  const Stripe = (await import("stripe")).default;
  const stripe = new Stripe(stripeKey);

  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    payment_method_types: ["card"],
    customer_email: rows[0].email,
    line_items: [{ price: priceId, quantity: 1 }],
    metadata: { userId },
    success_url: successUrl,
    cancel_url: cancelUrl,
  });

  return NextResponse.json({ checkoutUrl: session.url });
}
