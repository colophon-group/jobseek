import "server-only";

import { and, eq } from "drizzle-orm";
import { db } from "@/db";
import { paddleAccount, paddleSubscription } from "@/db/schema";
import { getPaddle } from "./client";
import { paddleEnvironment } from "./config";

/** Called before Better Auth deletes the user. Persist the deletion intent
 * first so concurrent checkout cannot create a new subscription during cleanup.
 * Failed provider calls leave the account intact and deletion can be retried. */
export async function preparePaddleAccountDeletion(userId: string): Promise<void> {
  if (!process.env.PADDLE_ENVIRONMENT) return;
  const environment = paddleEnvironment();
  const accounts = await db.select().from(paddleAccount).where(eq(paddleAccount.userId, userId));
  if (accounts.some(a => a.environment !== environment && (a.customerId || a.pendingTransactionId))) {
    throw new Error("Billing in another environment must be canceled before deleting this account");
  }
  const account = await db.transaction(async (tx) => {
    await tx.insert(paddleAccount).values({ userId, environment }).onConflictDoNothing();
    const [row] = await tx.select().from(paddleAccount).where(and(
      eq(paddleAccount.userId, userId), eq(paddleAccount.environment, environment),
    )).for("update");
    await tx.update(paddleAccount).set({ deletionRequested: true }).where(eq(paddleAccount.id, row.id));
    return row;
  });
  const subscriptions = await db.select({ id: paddleSubscription.id }).from(paddleSubscription)
    .where(eq(paddleSubscription.accountId, account.id));
  const ids = new Set(subscriptions.map(s => s.id));
  if (!account.pendingTransactionId && !ids.size) return;
  const paddle = getPaddle();
  if (account.pendingTransactionId) {
    const pending = await paddle.transactions.get(account.pendingTransactionId);
    if (pending.status === "draft" || pending.status === "ready") {
      // If checkout wins the race, Paddle refuses cancellation; fail closed
      // and retry deletion once its subscription is available.
      await paddle.transactions.update(pending.id, { status: "canceled" });
    } else if (pending.status !== "canceled") {
      if (!pending.subscriptionId) throw new Error("Awaiting checkout subscription before deletion");
      ids.add(pending.subscriptionId);
    }
  }
  for (const id of ids) {
    const subscription = await paddle.subscriptions.get(id);
    if (subscription.status !== "canceled") {
      await paddle.subscriptions.cancel(id, { effectiveFrom: "immediately" });
    }
  }
}
