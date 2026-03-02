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

export function middleware(request: NextRequest) {
  const locale = getLocale(request);
  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${request.nextUrl.pathname}`;
  return NextResponse.redirect(url);
}

export const config = {
  // Only match paths that do NOT start with a locale prefix, static assets,
  // API routes, or Next.js internals.  Locale-prefixed paths (e.g. /en/…)
  // skip the middleware entirely — no edge invocation needed.
  matcher: ["/((?!_next|api|flags|fonts|publicdomain|favicon\\.ico|en|de|fr|it|.*\\..*).*)" ],
};
