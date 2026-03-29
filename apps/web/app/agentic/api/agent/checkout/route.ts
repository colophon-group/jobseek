/**
 * POST /agentic/api/agent/checkout
 *
 * Creates a Stripe Checkout session for the given user and returns the URL.
 * The agent hands this URL to the user to complete payment in-browser.
 *
 * Body: { userId: string, email: string, successUrl: string, cancelUrl: string }
 * Authenticated via Bearer token (AGENT_API_KEY).
 *
 * Requires: STRIPE_SECRET_KEY, STRIPE_PRICE_ID
 */
import { NextRequest, NextResponse } from "next/server";
import { verifyAgentKey, agentUnauthorized } from "@/lib/agentic/agentAuth";

export async function POST(req: NextRequest) {
  if (!verifyAgentKey(req)) return agentUnauthorized();

  const stripeKey = process.env.STRIPE_SECRET_KEY;
  const priceId = process.env.STRIPE_PRICE_ID;
  if (!stripeKey || !priceId) {
    return NextResponse.json(
      { error: "Stripe is not configured on this server" },
      { status: 503 },
    );
  }

  let body: { userId?: string; email?: string; successUrl?: string; cancelUrl?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { userId, email, successUrl, cancelUrl } = body;
  if (!userId || !email || !successUrl || !cancelUrl) {
    return NextResponse.json(
      { error: "userId, email, successUrl, and cancelUrl are required" },
      { status: 400 },
    );
  }

  const Stripe = (await import("stripe")).default;
  const stripe = new Stripe(stripeKey);

  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    payment_method_types: ["card"],
    customer_email: email,
    line_items: [{ price: priceId, quantity: 1 }],
    metadata: { userId },
    success_url: successUrl,
    cancel_url: cancelUrl,
  });

  return NextResponse.json({ sessionId: session.id, url: session.url });
}
