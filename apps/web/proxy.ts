import { type NextRequest, NextResponse } from "next/server";
import { match } from "@formatjs/intl-localematcher";
import Negotiator from "negotiator";
import { defaultLocale, locales, isLocale } from "@/lib/i18n";

const COOKIE_NAME = "NEXT_LOCALE";
const LOGGED_IN_HINT_COOKIE = "logged_in";
const COMPANY_REQUEST_PATH = /^\/(en|de|fr|it)\/companies\/request$/;

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
  // The public IndexNow proof filename is derived from a secret at runtime and
  // therefore cannot be listed in the static matcher below. Let that one
  // configured dotted root path continue to the rewrite in next.config.ts.
  const indexNowKey = process.env.INDEXNOW_KEY;
  if (
    indexNowKey &&
    request.nextUrl.pathname === `/${indexNowKey}.txt`
  ) {
    return NextResponse.next();
  }

  const companyRequestMatch = request.nextUrl.pathname.match(
    COMPANY_REQUEST_PATH,
  );
  if (companyRequestMatch) {
    // Decide the anonymous continuation before the Cache Components app shell
    // can hydrate. Otherwise SalaryDisplayProvider starts getCurrencyRates(),
    // the page redirect redirects that Server Action response, and Next falls
    // back to a blank full-document navigation (#6043). The page still checks
    // the real httpOnly session for hinted visitors; this cookie is only the
    // same non-sensitive fast-path hint used by AppBootstrapProvider.
    if (request.cookies.has(LOGGED_IN_HINT_COOKIE)) {
      return NextResponse.next();
    }

    const returnPath = `${request.nextUrl.pathname}${request.nextUrl.search}`;
    const signInUrl = request.nextUrl.clone();
    signInUrl.pathname = `/${companyRequestMatch[1]}/sign-in`;
    signInUrl.search = "";
    signInUrl.searchParams.set("next", returnPath);
    return NextResponse.redirect(signInUrl);
  }

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
  // Only match paths that do NOT start with a locale prefix, a known static
  // asset/discovery route, an API route, or Next.js internals. Unknown dotted
  // root paths must still pass through the proxy: otherwise `[lang]` treats
  // the filename as an invalid locale and the dynamic root layout turns its
  // intended 404 into a 500. Redirecting to `/<locale>/<path>` reaches the
  // localized 404 surface correctly.
  //
  // `opengraph-image*` is excluded so `og:image` URLs that the Metadata
  // API generates against the root `app/opengraph-image.tsx` reach the
  // handler directly. Without this, Twitter / LinkedIn / Slack OG fetches
  // for non-(public) pages 308-redirect to `/<locale>/opengraph-image-<hash>`
  // and 404. See #2835 critic round 1.
  matcher: [
    "/((?!_next|api|mcp|flags|fonts|publicdomain|screenshots|\\.well-known|favicon\\.ico$|favicon-16x16\\.png$|favicon-32x32\\.png$|apple-touch-icon\\.png$|apple-touch-icon-[^/]+\\.png$|android-chrome-192x192\\.png$|android-chrome-512x512\\.png$|site\\.webmanifest$|BingSiteAuth\\.xml$|js_[^/]+\\.svg$|js_missing_screenshot_black\\.png$|js_missing_screenshot_white\\.png$|logo-dark\\.svg$|logo-light\\.svg$|opengraph-image|indexnow-key\\.txt$|llms\\.txt$|openapi\\.json$|openapi\\.yaml$|robots\\.txt$|sitemap\\.xml$|en|de|fr|it).*)",
    "/:lang(en|de|fr|it)/companies/request",
  ],
};
