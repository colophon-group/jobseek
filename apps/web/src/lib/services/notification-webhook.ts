import "server-only";
import { and, eq } from "drizzle-orm";
import type { WebhookEventPayload } from "resend";
import { db } from "@/db";
import { notificationDelivery, user, userPreferences } from "@/db/schema";
import { lockNotificationPolicyForUser } from "./notification-preferences";

/** Signed acceptance evidence closes ambiguous sends; never retry accepted mail. */
export async function reconcileNotificationWebhook(event: WebhookEventPayload) {
  if (!event.type.startsWith("email.") || !("tags" in event.data)) return;
  const data = event.data;
  const id = data.tags?.notification_delivery;
  const attempt = Number(data.tags?.notification_attempt);
  if (!id || !/^[0-9a-f-]{36}$/.test(id) || !Number.isSafeInteger(attempt) || attempt < 1) return;
  if (!['email.sent', 'email.delivered', 'email.delivery_delayed', 'email.bounced', 'email.complained', 'email.failed', 'email.suppressed'].includes(event.type)) return;
  const [delivery] = await db.select({ userId: notificationDelivery.userId }).from(notificationDelivery).where(eq(notificationDelivery.id, id));
  if (!delivery) return;
  await db.transaction(async tx => {
    await lockNotificationPolicyForUser(tx, delivery.userId);
    const [row] = await tx.select().from(notificationDelivery).where(eq(notificationDelivery.id, id)).for("update");
    if (!row || row.providerAttemptCount !== attempt || !["unknown", "sent"].includes(row.status)) return;
    if (row.providerMessageId && row.providerMessageId !== data.email_id) return;
    const now = new Date();
    const suppression = ["email.bounced", "email.complained", "email.suppressed"].includes(event.type);
    const failed = suppression || event.type === "email.failed";
    await tx.update(notificationDelivery).set({ status: "sent", providerMessageId: data.email_id,
      completedAt: row.completedAt ?? now, updatedAt: now,
      // Preserve a bounce/complaint marker if webhook events arrive out of order.
      lastErrorCode: failed ? event.type.replace(".", "_") : row.lastErrorCode,
    }).where(eq(notificationDelivery.id, id));
    if (suppression && row.lastErrorCode !== event.type.replace(".", "_")) {
      const [recipient] = await tx.select({ email: user.email }).from(user).where(eq(user.id, delivery.userId)).for("update");
      if (recipient && data.to.some(email => email.toLowerCase() === recipient.email.toLowerCase())) {
        await tx.update(userPreferences).set({ notificationsPaused: true, updatedAt: now })
          .where(and(eq(userPreferences.userId, delivery.userId), eq(userPreferences.notificationsPaused, false)));
      }
    }
  });
}
