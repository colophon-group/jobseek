"use server";

import { getNotificationWatchlistsForUser, setWatchlistNotificationModeForUser } from "@/lib/services/notification-watchlist-settings";
import { isWatchlistId } from "@/lib/watchlist-id";
import type { WatchlistNotificationMode } from "@/lib/notifications/settings-contract";
import { getSessionUserId } from "@/lib/sessionCache";
import {
  getNotificationPreferencesForUser,
  setNotificationsPausedForUser,
} from "@/lib/services/notification-preferences";

export async function getNotificationPreferences(locale = "en") {
  const userId = await getSessionUserId();
  if (!userId) return null;
  const [preferences, watchlists] = await Promise.all([getNotificationPreferencesForUser(userId), getNotificationWatchlistsForUser(userId, ["en", "de", "fr", "it"].includes(locale) ? locale : "en")]);
  return { ...preferences, watchlists };
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

export async function setWatchlistNotificationMode(watchlistId: string, mode: WatchlistNotificationMode) {
  const userId = await getSessionUserId();
  if (!userId) return { error: "not_authenticated" as const };
  if (!isWatchlistId(watchlistId) || !["off", "all", "narrowed"].includes(mode)) {
    return { error: "invalid_request" as const };
  }
  return setWatchlistNotificationModeForUser(userId, watchlistId, mode);
}
