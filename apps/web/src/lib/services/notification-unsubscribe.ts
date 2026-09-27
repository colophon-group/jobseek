import "server-only";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { notificationDelivery, user, userPreferences } from "@/db/schema";
import { lockNotificationPolicyForUser } from "./notification-preferences";
import { unsubscribeDeliveryId, verifyUnsubscribeToken } from "@/lib/notifications/unsubscribe-token";

export async function unsubscribeNotification(token: string, pause: boolean) {
  const id = unsubscribeDeliveryId(token);
  const secret = process.env.JOB_ALERTS_UNSUBSCRIBE_SECRET ?? "";
  if (!id || secret.length < 32) return null;
  const [delivery] = await db.select({ userId: notificationDelivery.userId })
    .from(notificationDelivery).where(eq(notificationDelivery.id, id));
  if (!delivery) return null;
  return db.transaction(async tx => {
    await lockNotificationPolicyForUser(tx, delivery.userId);
    const [recipient] = await tx.select({ email: user.email, locale: userPreferences.locale })
      .from(user).innerJoin(userPreferences, eq(userPreferences.userId, user.id))
      .where(eq(user.id, delivery.userId)).for("update");
    if (!recipient || !verifyUnsubscribeToken(token, recipient.email, secret)) return null;
    if (pause) await tx.update(userPreferences).set({ notificationsPaused: true, updatedAt: new Date() })
      .where(eq(userPreferences.userId, delivery.userId));
    // Database trigger sets the state-change floor only on a real transition.
    return { locale: recipient.locale };
  });
}
