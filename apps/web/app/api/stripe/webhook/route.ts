import { getStripe } from "@/lib/stripe/client";
import { applyStripeEvent } from "@/lib/stripe/webhooks";
import { logExternalError } from "@/lib/safe-external-error";

export async function POST(request: Request) {
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret?.startsWith("whsec_")) return Response.json({ error: "Webhook not configured" }, { status: 503 });
  const signature = request.headers.get("stripe-signature");
  if (!signature) return Response.json({ error: "Missing signature" }, { status: 400 });
  let stripe;
  try { stripe = getStripe(); } catch { return Response.json({ error: "Webhook not configured" }, { status: 503 }); }
  const body = await request.text();
  let event;
  try { event = stripe.webhooks.constructEvent(body, signature, secret); }
  catch { return Response.json({ error: "Invalid webhook" }, { status: 400 }); }
  try {
    await applyStripeEvent(event);
    return Response.json({ received: true });
  } catch (error) {
    logExternalError("error", { service: "external_http", operation: "stripe.webhook" }, error);
    return Response.json({ error: "Webhook processing failed" }, { status: 503 });
  }
}
