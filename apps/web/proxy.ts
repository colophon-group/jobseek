import { type NextRequest, NextResponse } from "next/server";
import { match } from "@formatjs/intl-localematcher";
import Negotiator from "negotiator";
import { defaultLocale, locales, isLocale } from "@/lib/i18n";

const COOKIE_NAME = "NEXT_LOCALE";

function getLocale(request: NextRequest): string {
  // 1. Explicit cookie from a previous locale switch
  const cookieLocale = request.cookies.get(COOKIE_NAME)?.value;
  if (cookieLocale && isLocale(cookieLocale)) return cookieLocale;

  // 2. Accept-Language negotiation
  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => {
    headers[key] = value;
  });
  const languages = new Negotiator({ headers })
    .languages()
    .filter((l) => l !== "*");
  return match(languages, locales as unknown as string[], defaultLocale);
}

export function proxy(request: NextRequest) {
  const cookieLocale = request.cookies.get(COOKIE_NAME)?.value;
  const locale = getLocale(request);
  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${request.nextUrl.pathname}`;
  const response = NextResponse.redirect(url);

  // Cache the redirect at Vercel's CDN when the chosen locale comes from
  // Accept-Language negotiation. Repeat requests with matching headers (most
  // bot/shared-link traffic on root URLs) then reuse the redirect without
  // re-invoking the proxy. We deliberately skip the cache when an
  // explicit NEXT_LOCALE cookie is set: that path varies per user and Vary:
  // Cookie would shard the cache by every session token. See issue #2642.
  if (!cookieLocale || !isLocale(cookieLocale)) {
    response.headers.set(
      "Cache-Control",
      "public, max-age=86400, s-maxage=86400",
    );
    response.headers.set("Vary", "Accept-Language");
  }

  return response;
}

export const config = {
  // Only match paths that do NOT start with a locale prefix, static assets,
  // API routes, or Next.js internals.  Locale-prefixed paths (e.g. /en/…)
  // skip the proxy entirely — no edge invocation needed.
  //
  // `opengraph-image*` is excluded so `og:image` URLs that the Metadata
  // API generates against the root `app/opengraph-image.tsx` reach the
  // handler directly. Without this, Twitter / LinkedIn / Slack OG fetches
  // for non-(public) pages 308-redirect to `/<locale>/opengraph-image-<hash>`
  // and 404. See #2835 critic round 1.
  matcher: ["/((?!_next|api|mcp|flags|fonts|publicdomain|favicon\\.ico|opengraph-image|en|de|fr|it|.*\\..*).*)" ],
};
