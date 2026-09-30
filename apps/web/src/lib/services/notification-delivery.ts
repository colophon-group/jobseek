import "server-only";
import { and, eq, inArray, ne, sql } from "drizzle-orm";
import { db } from "@/db";
import { notificationDelivery, notificationQuota, user, userPreferences, watchlist } from "@/db/schema";
import { siteConfig } from "@/content/config";
import { lockNotificationPolicyForUser } from "./notification-preferences";
import type { NotificationDeliveryPlan } from "@/lib/notifications/scheduler-core";
import type { JobAlertsConfig } from "@/lib/notifications/config";
import { isNotificationSendCooldownActive, MAX_NOTIFICATION_PROVIDER_ATTEMPTS } from "@/lib/notifications/policy";
import { calculateNotificationQuota } from "@/lib/notifications/scheduler-policy";
import { createUnsubscribeToken } from "@/lib/notifications/unsubscribe-token";
import { renderNotificationEmail } from "@/lib/notifications/render-email";
import { sendNotificationEmail } from "@/lib/notifications/provider";

import { validateNarrowedNotificationPostings } from "./notification-narrowing";

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];

async function recipientForPlan(tx: Transaction, plan: NotificationDeliveryPlan, config: JobAlertsConfig) {
  if (config.mode !== "live" && config.mode !== "internal") return null;
  if (config.mode === "internal" && !config.internalUserIds.includes(plan.userId)) return null;
  const [recipient] = await tx.select({
    email: user.email, verified: user.emailVerified, userUpdatedAt: user.updatedAt,
    locale: userPreferences.locale, paused: userPreferences.notificationsPaused,
    changedAt: userPreferences.notificationsStateChangedAt, updatedAt: userPreferences.updatedAt,
  }).from(user).innerJoin(userPreferences, eq(userPreferences.userId, user.id))
    .where(eq(user.id, plan.userId)).for("update");
  if (!recipient || !recipient.verified || recipient.paused ||
    recipient.changedAt > plan.plannedAt || recipient.updatedAt > plan.plannedAt ||
    recipient.userUpdatedAt > plan.plannedAt) return null;
  // Lock the current choices through submission; concurrent filter edits cannot
  // race the final check. Toggle/pause/unsubscribe share the policy lock too.
  const choices = await tx.select({ id: watchlist.id, enabled: watchlist.alertsEnabled,
    enabledAt: watchlist.alertsEnabledAt, updatedAt: watchlist.updatedAt, narrowedOnly: watchlist.alertsNarrowedOnly,
  }).from(watchlist).where(eq(watchlist.userId, plan.userId)).for("update");
  if (choices.some(w => w.updatedAt > plan.plannedAt || (w.enabledAt && w.enabledAt > plan.plannedAt))) return null;
  const enabled = new Set(choices.filter(w => w.enabled && w.enabledAt).map(w => w.id));
  if (!plan.displayPostings.length || plan.displayPostings.some(p =>
    p.matchedWatchlists.some(w => !enabled.has(w.id)))) return null;
  for (const choice of choices) {
    const postings = plan.displayPostings.filter(p => p.matchedWatchlists.some(w => w.id === choice.id));
    if (!postings.length) continue;
    const labels = postings.map(p => p.matchedWatchlists.find(w => w.id === choice.id)!);
    if (!choice.narrowedOnly) {
      if (labels.some(label => label.narrowedQueryVersionId)) return null;
      continue;
    }
    const version = labels[0]!.narrowedQueryVersionId;
    if (!version || labels.some(label => label.narrowedQueryVersionId !== version) ||
      !await validateNarrowedNotificationPostings(tx, { ownerId: plan.userId, watchlistId: choice.id,
        queryVersionId: version, postingIds: postings.map(p => p.id), plannedAt: plan.plannedAt,
      })) return null;
  }
  return recipient;
}

export type DeliveryOutcome = "sent" | "failed" | "unknown" | "deferred" | "cancelled" | "duplicate";

export async function deliverNotificationPlan(plan: NotificationDeliveryPlan, config: JobAlertsConfig): Promise<DeliveryOutcome> {
  if (config.mode !== "internal" && config.mode !== "live") return "cancelled";
  const prepared = await db.transaction(async tx => {
    await lockNotificationPolicyForUser(tx, plan.userId);
    const [row] = await tx.select().from(notificationDelivery).where(eq(notificationDelivery.id, plan.deliveryId)).for("update");
    if (!row || row.userId !== plan.userId || row.idempotencyKey !== plan.idempotencyKey ||
      row.windowEnd.getTime() !== plan.windowEnd.getTime() || row.status !== "pending" || row.updatedAt.getTime() !== plan.plannedAt.getTime() ||
      row.providerAttemptCount >= MAX_NOTIFICATION_PROVIDER_ATTEMPTS) return { outcome: "duplicate" as const };
    const recipient = await recipientForPlan(tx, plan, config);
    const now = new Date();
    if (!recipient) {
      await tx.update(notificationDelivery).set({ status: "failed", lastErrorCode: "preferences_changed", updatedAt: now }).where(eq(notificationDelivery.id, row.id));
      return { outcome: "cancelled" as const };
    }
    // Recheck under the owner lock: plans can outlive their scheduler snapshot.
    // Another period's committed unknown barrier excludes competing plans even
    // between preparation and submission, before acceptance starts a cooldown.
    const [history] = await tx.select({
      lastSentAt: sql<Date | null>`max(${notificationDelivery.completedAt})
        FILTER (WHERE ${notificationDelivery.status} = 'sent')`.mapWith(notificationDelivery.completedAt),
      unknownCount: sql<number>`count(*) FILTER (WHERE ${notificationDelivery.status} = 'unknown')`.mapWith(Number),
    }).from(notificationDelivery).where(and(
      eq(notificationDelivery.userId, plan.userId),
      ne(notificationDelivery.id, row.id),
      inArray(notificationDelivery.status, ["sent", "unknown"]),
    ));
    if (history!.unknownCount > 0 || isNotificationSendCooldownActive({
      cadence: plan.cadence, lastSentAt: history!.lastSentAt, now: new Date(),
    })) {
      // Keep the pending period recoverable; do not advance its floor, reserve
      // quota, consume an attempt, or confuse cooldown with an empty interval.
      return { outcome: "deferred" as const };
    }
    // Both UTC buckets and the durable unknown barrier commit atomically.
    // Failed and uncertain attempts retain reservations, preserving the reserve
    // even when the provider or process fails. No Redis eviction can reset caps.
    await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtextextended('jobseek:notification-quota', 0))`);
    const periods = [`day:${now.toISOString().slice(0, 10)}`, `month:${now.toISOString().slice(0, 7)}`];
    const counts = await tx.select().from(notificationQuota).where(inArray(notificationQuota.period, periods));
    const quota = calculateNotificationQuota({ state: {
      dailyCap: config.dailyCap, monthlyCap: config.monthlyCap,
      dailyUsed: counts.find(r => r.period === periods[0])?.used ?? 0,
      monthlyUsed: counts.find(r => r.period === periods[1])?.used ?? 0,
    }, requested: 1, now });
    if (!quota.allowed) {
      await tx.update(notificationDelivery).set({ status: "quota_deferred", deferredUntil: quota.deferredUntil, updatedAt: now }).where(eq(notificationDelivery.id, row.id));
      return { outcome: "deferred" as const };
    }
    for (const period of periods) {
      await tx.insert(notificationQuota).values({ period, used: 1 }).onConflictDoUpdate({ target: notificationQuota.period, set: { used: sql`${notificationQuota.used} + 1` } });
    }
    const attempt = row.providerAttemptCount + 1;
    await tx.update(notificationDelivery).set({ status: "unknown", providerAttemptCount: attempt,
      lastProviderAttemptAt: now, lastErrorCode: null, updatedAt: now,
    }).where(eq(notificationDelivery.id, row.id));
    return { attempt };
  });
  if (prepared.outcome) return prepared.outcome;

  // Unknown is already committed. A crash or transaction rollback during the
  // external call therefore cannot reopen the message for automatic retry.
  return db.transaction(async tx => {
    await lockNotificationPolicyForUser(tx, plan.userId);
    const [row] = await tx.select().from(notificationDelivery).where(eq(notificationDelivery.id, plan.deliveryId)).for("update");
    if (!row || row.status !== "unknown" || row.providerAttemptCount !== prepared.attempt) return "duplicate";
    const recipient = await recipientForPlan(tx, plan, config);
    if (!recipient || row.lastProviderAttemptAt?.toISOString().slice(0, 10) !== new Date().toISOString().slice(0, 10)) {
      await tx.update(notificationDelivery).set({ status: "failed", lastErrorCode: "preferences_changed", updatedAt: new Date() }).where(eq(notificationDelivery.id, row.id));
      return "cancelled";
    }
    const token = createUnsubscribeToken(plan.deliveryId, recipient.email, process.env.JOB_ALERTS_UNSUBSCRIBE_SECRET!);
    const unsubscribeUrl = `${siteConfig.url}/api/notifications/unsubscribe?token=${encodeURIComponent(token)}`;
    const result = await sendNotificationEmail({
      to: recipient.email, deliveryId: plan.deliveryId, attempt: prepared.attempt,
      idempotencyKey: plan.idempotencyKey, unsubscribeUrl,
      email: renderNotificationEmail({ plan, locale: recipient.locale, origin: siteConfig.url, unsubscribeUrl }),
    });
    const now = new Date();
    await tx.update(notificationDelivery).set(result.status === "sent" ? {
      status: "sent", providerMessageId: result.messageId, completedAt: now, updatedAt: now,
    } : { status: result.status, lastErrorCode: result.errorCode, updatedAt: now })
      .where(and(eq(notificationDelivery.id, row.id), eq(notificationDelivery.status, "unknown")));
    return result.status;
  });
}

export async function readNotificationQuota(config: JobAlertsConfig, now = new Date()) {
  const periods = [`day:${now.toISOString().slice(0, 10)}`, `month:${now.toISOString().slice(0, 7)}`];
  const rows = await db.select().from(notificationQuota).where(inArray(notificationQuota.period, periods));
  return { dailyCap: config.dailyCap, monthlyCap: config.monthlyCap,
    dailyUsed: rows.find(r => r.period === periods[0])?.used ?? 0,
    monthlyUsed: rows.find(r => r.period === periods[1])?.used ?? 0 };
}
