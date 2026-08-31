"use server";

import { getSessionUserId } from "@/lib/sessionCache";
import {
  getNotificationPreferencesForUser,
  setNotificationsPausedForUser,
} from "@/lib/services/notification-preferences";

export async function getNotificationPreferences() {
  const userId = await getSessionUserId();
  if (!userId) return null;
  return getNotificationPreferencesForUser(userId);
}

export async function setNotificationsPaused(
  notificationsPaused: boolean,
): Promise<
  | Awaited<ReturnType<typeof setNotificationsPausedForUser>>
  | { error: "not_authenticated" | "invalid_request" }
> {
  if (typeof notificationsPaused !== "boolean") {
    return { error: "invalid_request" };
  }
  const userId = await getSessionUserId();
  if (!userId) return { error: "not_authenticated" };
  return setNotificationsPausedForUser(userId, notificationsPaused);
}
