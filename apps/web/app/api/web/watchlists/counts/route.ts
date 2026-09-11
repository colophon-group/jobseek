import { NextResponse } from "next/server";

import { locales } from "@/lib/i18n";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";
import { getUserWatchlistActivityPreviewsForUser } from "@/lib/services/watchlists";
import { watchlistActivityLimiter } from "@/lib/rate-limit";
import { logExternalError } from "@/lib/safe-external-error";

export async function GET(request: Request) {
  const userId = await getSessionUserIdFromHeaders(request.headers);
  if (!userId) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
    const { success, reset } = await watchlistActivityLimiter.limit(userId);
    if (!success) {
      return NextResponse.json(
        { error: "too_many_requests" },
        {
          status: 429,
          headers: {
            "Cache-Control": "private, no-store",
            "Retry-After": String(Math.max(1, Math.ceil((reset - Date.now()) / 1_000))),
          },
        },
      );
    }
  } catch (err) {
    // The preview is optional, but the exhaustive facet query is not safe to
    // expose when both its replay limiter and Redis result cache are down.
    logExternalError(
      "warn",
      { service: "redis", operation: "watchlist_activity_rate_limit" },
      err,
    );
    return NextResponse.json(
      { error: "temporarily_unavailable" },
      {
        status: 503,
        headers: {
          "Cache-Control": "private, no-store",
          "Retry-After": "30",
        },
      },
    );
  }

  const requestedLocale = new URL(request.url).searchParams.get("locale");
  const locale = requestedLocale && locales.includes(requestedLocale as (typeof locales)[number])
    ? requestedLocale
    : "en";
  const previews = await getUserWatchlistActivityPreviewsForUser(userId, locale);
  const counts = Object.fromEntries(
    Object.entries(previews).map(([watchlistId, preview]) => [
      watchlistId,
      preview.activeJobCount,
    ]),
  );

  return NextResponse.json(
    { counts, previews },
    { headers: { "Cache-Control": "private, no-store" } },
  );
}
