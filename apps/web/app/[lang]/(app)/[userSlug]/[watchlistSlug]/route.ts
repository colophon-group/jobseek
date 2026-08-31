import { type NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/sessionCache";
import { getOwnedWatchlistByLegacyPath } from "@/lib/services/watchlists";
import { defaultLocale, isLocale } from "@/lib/i18n";
import { staticMissingResourceDocument } from "@/lib/missing-resource-recovery";
import {
  WATCHLIST_SELECTION_COOKIE,
  encodeWatchlistSelection,
  watchlistSelectionCookieOptions,
} from "@/lib/watchlist-selection";

type RouteContext = {
  params: Promise<{
    lang: string;
    userSlug: string;
    watchlistSlug: string;
  }>;
};

function privateNotFound(locale: string): NextResponse {
  return new NextResponse(
    staticMissingResourceDocument("watchlist", locale),
    {
      status: 404,
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Language": locale,
        "Content-Type": "text/html; charset=utf-8",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, follow",
      },
    },
  );
}

/**
 * Bounded compatibility for bookmarks to the former public detail URL.
 * Anonymous, absent, and cross-owner requests intentionally share one 404.
 */
export async function GET(
  request: NextRequest,
  { params }: RouteContext,
): Promise<NextResponse> {
  const { lang, userSlug, watchlistSlug } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const session = await getSession();
  if (!session) return privateNotFound(locale);

  const detail = await getOwnedWatchlistByLegacyPath(
    userSlug,
    watchlistSlug,
    session.user.id,
  );
  if (!detail) return privateNotFound(locale);

  const response = NextResponse.redirect(
    new URL(`/${locale}/watchlists`, request.url),
    307,
  );
  response.headers.set("Cache-Control", "private, no-store");
  response.cookies.set(
    WATCHLIST_SELECTION_COOKIE,
    encodeWatchlistSelection(session.user.id, detail.id),
    watchlistSelectionCookieOptions,
  );
  return response;
}
