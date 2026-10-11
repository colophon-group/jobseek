import Stripe from "stripe";
import { billingMaintenanceUpdate } from "../src/lib/stripe/maintenance";
import availability from "../src/config/service-availability.json";

async function main() {
  const action = process.argv[2];
  if (!["audit", "pause", "resume"].includes(action)) throw new Error("Invalid action");
  if (process.env.STRIPE_ENVIRONMENT !== "production" ||
      !/^(sk|rk)_live_/.test(process.env.STRIPE_SECRET_KEY ?? "") ||
      !process.env.STRIPE_PRO_PRICE_ID?.startsWith("price_")) throw new Error("Production Stripe configuration required");
  if (action === "pause" && !availability.subscriptionBillingPaused) throw new Error("Enable the billing pause control first");
  if (action === "resume" && availability.subscriptionBillingPaused) throw new Error("Clear the billing pause control first");
  const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
    apiVersion: "2026-08-26.dahlia", maxNetworkRetries: 2, timeout: 20_000,
  });
  const subscriptions: Stripe.Subscription[] = [];
  let inspected = 0;
  // Bound inventory before any writes, and restrict changes to Jobseek's price.
  for await (const subscription of stripe.subscriptions.list({ status: "all", limit: 100 })) {
    if (++inspected > 1_000) throw new Error("Inventory exceeds bounded control");
    if (!subscription.livemode) throw new Error("Unexpected sandbox record");
    if (["canceled", "incomplete_expired"].includes(subscription.status)) continue;
    if (subscription.items.data.some(item => item.price.id === process.env.STRIPE_PRO_PRICE_ID)) {
      if (subscription.items.has_more || subscription.items.data.length !== 1 || subscription.schedule) {
        throw new Error("Complex subscription requires separate review");
      }
      subscriptions.push(subscription);
    }
  }
  let changed = 0;
  if (action !== "audit") {
    for (const previous of subscriptions) {
      const current = await stripe.subscriptions.retrieve(previous.id);
      if (!current.livemode || current.items.has_more || current.items.data.length !== 1 ||
          current.items.data[0].price.id !== process.env.STRIPE_PRO_PRICE_ID || current.schedule ||
          ["canceled", "incomplete_expired"].includes(current.status)) throw new Error("Subscription changed during control");
      const update = billingMaintenanceUpdate(current, action as "pause" | "resume");
      if (!update) continue;
      const updated = await stripe.subscriptions.update(current.id, update);
      if (action === "pause" ? updated.pause_collection?.behavior !== "void" : updated.pause_collection !== null) {
        throw new Error("Provider did not confirm collection state");
      }
      changed += 1;
    }
  }
  console.info(JSON.stringify({ event: "subscription_billing_control", action, subscriptions: subscriptions.length,
    collectionPausedBefore: subscriptions.filter(value => value.pause_collection).length, changed }));
}

main().catch(() => {
  // Payment errors can include request/customer data; keep operator logs aggregate.
  console.error("subscription_billing_control_failed");
  process.exitCode = 1;
});
