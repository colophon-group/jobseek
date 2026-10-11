import type Stripe from "stripe";

const OWNER_KEY = "jobseek_billing_maintenance";
const OWNER_VALUE = "narrowed-maintenance-v1";

/** Only resume collection pauses created by this operator control. */
export function billingMaintenanceUpdate(
  subscription: Stripe.Subscription,
  action: "pause" | "resume",
): Stripe.SubscriptionUpdateParams | null {
  if (action === "pause") {
    if (subscription.pause_collection) return null;
    return {
      pause_collection: { behavior: "void" },
      metadata: { [OWNER_KEY]: OWNER_VALUE },
    };
  }
  if (subscription.metadata[OWNER_KEY] !== OWNER_VALUE ||
      subscription.pause_collection?.behavior !== "void" ||
      subscription.pause_collection.resumes_at) return null;
  return { pause_collection: "", metadata: { [OWNER_KEY]: "" } };
}
