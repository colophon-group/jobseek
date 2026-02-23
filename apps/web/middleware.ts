import { type NextRequest, NextResponse } from "next/server";
import { defaultLocale, isLocale } from "@/lib/i18n";

function getPreferredLocale(request: NextRequest): string {
  const acceptLanguage = request.headers.get("accept-language");
  if (!acceptLanguage) return defaultLocale;

  const preferred = acceptLanguage
    .split(",")
    .map((lang) => {
      const [code, q] = lang.trim().split(";q=");
      return { code: code.split("-")[0].toLowerCase(), q: q ? parseFloat(q) : 1 };
    })
    .sort((a, b) => b.q - a.q)
    .find((lang) => isLocale(lang.code));

  return preferred?.code ?? defaultLocale;
}

export function middleware(request: NextRequest) {
  const locale = getPreferredLocale(request);
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
