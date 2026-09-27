import "server-only";

import { and, eq, sql } from "drizzle-orm";
import type { EventEntity, SubscriptionCreatedNotification, SubscriptionNotification } from "@paddle/paddle-node-sdk";
import { db } from "@/db";
import { paddleAccount, paddleSubscription } from "@/db/schema";
import { paddleEnvironment, paddlePriceIds } from "./config";
import { MANAGED_SUBSCRIPTION_EVENTS, subscriptionState } from "./state";

export async function applyPaddleEvent(event: EventEntity): Promise<void> {
  if (!MANAGED_SUBSCRIPTION_EVENTS.has(event.eventType)) return;
  const data = event.data as SubscriptionNotification;
  const environment = paddleEnvironment();
  const prices = paddlePriceIds();

  await db.transaction(async (tx) => {
    // Lock the account before either checkout or webhook changes it. All
    // subscription events for an account serialize against this same row.
    const [known] = await tx.select({ accountId: paddleSubscription.accountId })
      .from(paddleSubscription).where(eq(paddleSubscription.id, data.id)).limit(1);
    const candidateId = known?.accountId ?? data.customData?.jobseek_account_id;
    if (typeof candidateId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(candidateId)) return;
    const [account] = await tx.select().from(paddleAccount).where(and(
      eq(paddleAccount.id, candidateId), eq(paddleAccount.environment, environment),
    )).for("update");
    if (!account) return;

    // Re-read after locking: another delivery may have bound it meanwhile.
    const [existing] = await tx.select().from(paddleSubscription)
      .where(eq(paddleSubscription.id, data.id)).limit(1);
    if (!existing) {
      if (event.eventType !== "subscription.created") {
        // Paddle may deliver activated/updated before created. Retry after
        // the created event establishes the authoritative transaction link.
        if (account.pendingTransactionId) throw new Error("Awaiting Paddle subscription.created");
        return;
      }
      const created = event.data as SubscriptionCreatedNotification;
      if (!account.pendingTransactionId || !account.pendingPriceId || created.transactionId !== account.pendingTransactionId) return;
      if (account.customerId && account.customerId !== data.customerId) {
        throw new Error("Paddle customer does not match checkout account");
      }
      await tx.update(paddleAccount).set({
        customerId: data.customerId,
        pendingTransactionId: null,
        pendingPriceId: null,
        // Any previous subscription consumes eligibility, even when its first
        // observed state is canceled or it was created without a trial.
        trialUsedAt: account.trialUsedAt ?? new Date(event.occurredAt),
      }).where(eq(paddleAccount.id, account.id));
    }

    const expectedPriceId = existing?.expectedPriceId ?? account.pendingPriceId!;
    const state = subscriptionState(data, [prices.trial, prices.returning].filter(id => id === expectedPriceId));
    await tx.insert(paddleSubscription).values({
      id: data.id, accountId: account.id, expectedPriceId, ...state, eventOccurredAt: event.occurredAt,
    }).onConflictDoUpdate({
      target: paddleSubscription.id,
      set: { ...state, eventOccurredAt: event.occurredAt, updatedAt: new Date() },
      setWhere: sql`${paddleSubscription.eventOccurredAt} < ${event.occurredAt}::timestamptz`,
    });
  });
}
