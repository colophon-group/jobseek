import type { SubscriptionNotification } from "@paddle/paddle-node-sdk";

export const MANAGED_SUBSCRIPTION_EVENTS = new Set([
  "subscription.created", "subscription.updated", "subscription.activated",
  "subscription.trialing", "subscription.past_due", "subscription.paused",
  "subscription.resumed", "subscription.canceled",
]);

export function subscriptionState(data: SubscriptionNotification, allowedPriceIds: string[]) {
  const item = data.items.length === 1 ? data.items[0] : undefined;
  const priceId = item?.price?.id ?? null;
  const eligible = Boolean(priceId && allowedPriceIds.includes(priceId) && item?.quantity === 1);
  const periodEnd = data.currentBillingPeriod?.endsAt ??
    (data.status === "trialing" ? item?.trialDates?.endsAt : null);
  return {
    status: data.status,
    priceId,
    entitled: eligible && (data.status === "trialing" || data.status === "active"),
    currentPeriodEnd: periodEnd ? new Date(periodEnd) : null,
    scheduledCancelAt: data.scheduledChange?.action === "cancel"
      ? new Date(data.scheduledChange.effectiveAt) : null,
  };
}
