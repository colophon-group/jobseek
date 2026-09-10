import "server-only";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { cached } from "@/lib/cache";
import { CACHE_TTL_SHORT } from "@/lib/cache-ttl";
import { withDbRetry } from "@/lib/db-retry";
import { getSearchClient } from "@/lib/search/typesense-client";
import {
  canResolveCompanyBySlugFromEnv,
  isSafeCompanySlug,
} from "@/lib/services/company-detail-lookup";

export function publicWatchlistRouteStatusCacheKey(
  userSlug: string,
  watchlistSlug: string,
): string {
  return `public-resource-status:watchlist:${userSlug}:${watchlistSlug}`;
}

/**
 * Cheap, authoritative existence check for the document-request boundary.
 *
 * This deliberately throws when Typesense is unavailable. The proxy then
 * fails open to the page's existing graceful-degradation path instead of
 * turning a search outage into a site-wide wave of false 404 responses.
 */
export async function hasPublicCompanyRoute(slug: string): Promise<boolean> {
  if (!isSafeCompanySlug(slug)) return false;
  // Deterministic secretless builds have no backing services; in that
  // environment every dynamic entity is absent by construction. A configured
  // client that later fails still throws and is handled fail-open by proxy.
  if (!canResolveCompanyBySlugFromEnv(process.env)) return false;

  return cached(
    `public-resource-status:company:${slug}`,
    async () => {
      const result = await getSearchClient()
        .collections("company")
        .documents()
        .search({
          q: "*",
          filter_by: `slug:=${slug}`,
          include_fields: "id",
          per_page: 1,
        });
      return (result.found ?? result.hits?.length ?? 0) > 0;
    },
    { ttl: CACHE_TTL_SHORT },
  );
}

/**
 * Public-only watchlist lookup used for anonymous document requests.
 * Private and absent rows both return false, preserving non-disclosure.
 */
export async function hasPublicWatchlistRoute(
  userSlug: string,
  watchlistSlug: string,
): Promise<boolean> {
  if (!process.env.DATABASE_URL) return false;

  return cached(
    publicWatchlistRouteStatusCacheKey(userSlug, watchlistSlug),
    async () => {
      const rows = await withDbRetry(
        () =>
          db.execute<{ [key: string]: unknown; route_exists: boolean }>(sql`
            SELECT EXISTS (
              SELECT 1
              FROM watchlist w
              JOIN "user" u ON u.id = w.user_id
              WHERE (u.username = ${userSlug} OR u.display_username = ${userSlug})
                AND w.slug = ${watchlistSlug}
                AND w.is_public = true
            ) AS route_exists
          `),
        { label: `publicWatchlistRouteStatus[${userSlug}/${watchlistSlug}]` },
      );
      return Boolean(
        (rows as unknown as Array<{ route_exists: boolean }>)[0]?.route_exists,
      );
    },
    { ttl: CACHE_TTL_SHORT },
  );
}

/**
 * Owner-only compatibility check for authenticated legacy-route requests.
 * Grandfathered public rows intentionally do not pass for other users: during
 * the private-route transition, public/private/missing rows must remain
 * indistinguishable to every non-owner. The caller must supply a user id from
 * a verified Better Auth session; this result is never shared-cached.
 */
export async function hasWatchlistRouteForViewer(
  userSlug: string,
  watchlistSlug: string,
  viewerUserId: string,
): Promise<boolean> {
  if (!process.env.DATABASE_URL) return false;

  const rows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; route_exists: boolean }>(sql`
        SELECT EXISTS (
          SELECT 1
          FROM watchlist w
          JOIN "user" u ON u.id = w.user_id
          WHERE (u.username = ${userSlug} OR u.display_username = ${userSlug})
            AND w.slug = ${watchlistSlug}
            AND w.user_id = ${viewerUserId}
        ) AS route_exists
      `),
    { label: `watchlistRouteForViewer[${userSlug}/${watchlistSlug}]` },
  );
  return Boolean(
    (rows as unknown as Array<{ route_exists: boolean }>)[0]?.route_exists,
  );
}
