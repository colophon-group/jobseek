import "server-only";
import { and, asc, eq } from "drizzle-orm";
import { db } from "@/db";
import { aiFilterConfiguration, aiFilterQueryVersion, userPreferences, watchlist } from "@/db/schema";
import type { NotificationWatchlistSetting, WatchlistNotificationMode } from "@/lib/notifications/settings-contract";
import { lockNotificationPolicyForUser } from "./notification-preferences";
import { resolveLocationSlugs } from "./locations";
import { resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "./taxonomy";
import type { WatchlistFilters } from "@/lib/watchlist-matcher-contract";
import { getNotificationNarrowing } from "./notification-narrowing";

export async function getNotificationWatchlistsForUser(ownerId: string, locale = "en"): Promise<NotificationWatchlistSetting[]> {
  const rows = await db.select({ id: watchlist.id, title: watchlist.title, filters: watchlist.filters,
    enabled: watchlist.alertsEnabled, narrowed: watchlist.alertsNarrowedOnly,
    prompt: aiFilterQueryVersion.queryText, status: aiFilterConfiguration.status,
  }).from(watchlist).leftJoin(aiFilterConfiguration, and(
    eq(aiFilterConfiguration.watchlistId, watchlist.id), eq(aiFilterConfiguration.ownerId, ownerId),
  )).leftJoin(aiFilterQueryVersion, and(
    eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
    eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
  )).where(eq(watchlist.userId, ownerId)).orderBy(asc(watchlist.title), asc(watchlist.id));
  const filters = rows.map(row => row.filters as WatchlistFilters);
  const slugs = (key: "locationSlugs" | "occupationSlugs" | "senioritySlugs" | "technologySlugs") =>
    [...new Set(filters.flatMap(filter => filter[key] ?? []))];
  // Resolve each taxonomy once for the whole page; a label outage must not
  // prevent users from changing notification preferences.
  const labels = await Promise.allSettled([
    resolveLocationSlugs(slugs("locationSlugs"), locale),
    resolveOccupationSlugs(slugs("occupationSlugs"), locale),
    resolveSenioritySlugs(slugs("senioritySlugs"), locale),
    resolveTechnologySlugs(slugs("technologySlugs")),
  ]);
  function values<T>(result: PromiseSettledResult<Map<string, T>>, keys: string[] | undefined): T[] {
    return result.status === "fulfilled" ? (keys ?? []).flatMap(key => {
      const value = result.value.get(key); return value ? [value] : [];
    }) : [];
  }
  return rows.map((row, index) => ({ id: row.id, title: row.title,
    filterPreview: { filters: filters[index]!,
      locations: values(labels[0], filters[index]!.locationSlugs),
      occupations: values(labels[1], filters[index]!.occupationSlugs),
      seniorities: values(labels[2], filters[index]!.senioritySlugs),
      technologies: values(labels[3], filters[index]!.technologySlugs),
    },
    mode: !row.enabled ? "off" : row.narrowed ? "narrowed" : "all",
    prompt: row.prompt, narrowingAvailable: row.status === "enabled" && Boolean(row.prompt),
  }));
}

export async function setWatchlistNotificationModeForUser(ownerId: string, watchlistId: string, mode: WatchlistNotificationMode) {
  return db.transaction(async tx => {
    await lockNotificationPolicyForUser(tx, ownerId);
    const [current] = await tx.select({ enabled: watchlist.alertsEnabled, narrowed: watchlist.alertsNarrowedOnly })
      .from(watchlist).where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, ownerId))).for("update").limit(1);
    if (!current) return { error: "not_found" as const };
    await tx.insert(userPreferences).values({ userId: ownerId }).onConflictDoNothing({ target: userPreferences.userId });
    const [preferences] = await tx.select({ paused: userPreferences.notificationsPaused }).from(userPreferences)
      .where(eq(userPreferences.userId, ownerId)).limit(1);
    if (preferences?.paused) return { error: "notifications_paused" as const };
    if (mode === "narrowed" && !await getNotificationNarrowing(ownerId, watchlistId, tx)) {
      return { error: "narrowing_unavailable" as const };
    }
    const enabled = mode !== "off";
    const narrowed = mode === "off" ? current.narrowed : mode === "narrowed";
    if (current.enabled !== enabled || current.narrowed !== narrowed) {
      const now = new Date();
      await tx.update(watchlist).set({ alertsEnabled: enabled, alertsNarrowedOnly: narrowed,
        // A changed selection begins a fresh interval, never a historical catch-up.
        alertsEnabledAt: enabled ? now : null, updatedAt: now,
      }).where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, ownerId)));
    }
    return { mode };
  });
}
