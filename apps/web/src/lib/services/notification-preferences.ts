import "server-only";

import { eq, sql, type SQL } from "drizzle-orm";

import { db } from "@/db";
import { userPreferences } from "@/db/schema";
import type { NotificationCadence } from "@/lib/notifications/contracts";
import {
  DEFAULT_NOTIFICATION_CADENCE,
  resolveNotificationCadence,
  transitionNotificationPause,
} from "@/lib/notifications/policy";

type NotificationPolicyLockExecutor = {
  execute(query: SQL): PromiseLike<unknown>;
};

export type NotificationPreferences = Readonly<{
  cadence: NotificationCadence;
  notificationsPaused: boolean;
  notificationsStateChangedAt: Date | null;
}>;

export type NotificationPauseMutationResult = Readonly<{
  changed: boolean;
  notificationsPaused: boolean;
  notificationsStateChangedAt: Date;
}>;

/**
 * Pause/resume and per-watchlist alert toggles take the same transaction lock.
 * This prevents a concurrent toggle from slipping through while pause wins.
 */
export async function lockNotificationPolicyForUser(
  executor: NotificationPolicyLockExecutor,
  userId: string,
): Promise<void> {
  await executor.execute(sql`
    SELECT pg_advisory_xact_lock(
      hashtextextended(${`jobseek:notification-policy:${userId}`}, 0)
    )
  `);
}

export async function getNotificationPreferencesForUser(
  userId: string,
): Promise<NotificationPreferences> {
  const [row] = await db
    .select({
      cadence: userPreferences.notificationCadence,
      notificationsPaused: userPreferences.notificationsPaused,
      notificationsStateChangedAt:
        userPreferences.notificationsStateChangedAt,
    })
    .from(userPreferences)
    .where(eq(userPreferences.userId, userId))
    .limit(1);

  return {
    cadence: resolveNotificationCadence(
      row?.cadence ?? DEFAULT_NOTIFICATION_CADENCE,
    ),
    notificationsPaused: row?.notificationsPaused ?? false,
    notificationsStateChangedAt: row?.notificationsStateChangedAt ?? null,
  };
}

/**
 * Persist one idempotent global pause transition without touching the
 * underlying watchlist alert preferences. A real resume records the floor
 * that later window calculation must honor.
 */
export async function setNotificationsPausedForUser(
  userId: string,
  notificationsPaused: boolean,
  changedAt = new Date(),
): Promise<NotificationPauseMutationResult> {
  return db.transaction(async (tx) => {
    await lockNotificationPolicyForUser(tx, userId);

    // Preferences are normally materialized during bootstrap. The insert
    // keeps this mutation safe for older or partially initialized accounts.
    await tx
      .insert(userPreferences)
      .values({
        userId,
        notificationCadence: DEFAULT_NOTIFICATION_CADENCE,
        notificationsPaused: false,
        notificationsStateChangedAt: changedAt,
      })
      .onConflictDoNothing({ target: userPreferences.userId });

    const [current] = await tx
      .select({
        notificationsPaused: userPreferences.notificationsPaused,
        notificationsStateChangedAt:
          userPreferences.notificationsStateChangedAt,
      })
      .from(userPreferences)
      .where(eq(userPreferences.userId, userId))
      .for("update")
      .limit(1);

    if (!current) {
      throw new Error("Notification preferences could not be initialized");
    }

    const next = transitionNotificationPause(
      current,
      notificationsPaused,
      changedAt,
    );
    if (next === current) {
      return { changed: false, ...current };
    }

    await tx
      .update(userPreferences)
      .set({
        notificationsPaused: next.notificationsPaused,
        notificationsStateChangedAt: next.notificationsStateChangedAt,
        updatedAt: changedAt,
      })
      .where(eq(userPreferences.userId, userId));

    return { changed: true, ...next };
  });
}
