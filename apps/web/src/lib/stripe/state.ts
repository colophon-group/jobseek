import type Stripe from "stripe";

export function stripeId(value: string | { id: string } | null | undefined): string | null {
  return typeof value === "string" ? value : value?.id ?? null;
}

export function subscriptionState(data: Stripe.Subscription, expectedPriceId: string, livemode: boolean) {
  const item = data.items.data.length === 1 && !data.items.has_more ? data.items.data[0] : undefined;
  const price = item?.price;
  const eligible = data.livemode === livemode && price?.id === expectedPriceId && price.livemode === livemode &&
    price.currency === "usd" && price.unit_amount === 1000 && price.recurring?.interval === "month" &&
    price.recurring.interval_count === 1 && item?.quantity === 1 && !data.pause_collection;
  const periodEnd = item?.current_period_end;
  const end = periodEnd ? Math.min(periodEnd, data.cancel_at ?? Infinity) : null;
  return {
    status: data.status,
    priceId: price?.id ?? null,
    entitled: Boolean(eligible && (data.status === "active" || data.status === "trialing")),
    currentPeriodEnd: end ? new Date(end * 1000) : null,
    scheduledCancelAt: data.cancel_at ? new Date(data.cancel_at * 1000)
      : data.cancel_at_period_end && periodEnd ? new Date(periodEnd * 1000) : null,
  };
}
