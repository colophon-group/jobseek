import { type NextRequest, NextResponse } from "next/server";
import { locales, defaultLocale, isLocale } from "@/lib/i18n";

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
  const { pathname } = request.nextUrl;

  // Check if pathname already has a locale prefix
  const maybeLocale = pathname.split("/")[1];

  if (isLocale(maybeLocale)) {
    // Persist the locale in a cookie so the root layout can set <html lang>
    const response = NextResponse.next();
    response.cookies.set("locale", maybeLocale, { path: "/", maxAge: 31536000 });
    return response;
  }

  // Redirect to locale-prefixed path
  const locale = getPreferredLocale(request);
  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${pathname}`;
  const response = NextResponse.redirect(url);
  response.cookies.set("locale", locale, { path: "/", maxAge: 31536000 });
  return response;
}

export const config = {
  // Only run on paths that need locale handling:
  // - bare `/` (root redirect)
  // - `/en/...`, `/de/...`, `/fr/...`, `/it/...` (set locale cookie)
  // - bare paths without locale prefix like `/how-we-index` (redirect to prefixed)
  // Excludes: _next, api, handler, static files, flags, fonts, images
  matcher: ["/((?!_next|api|handler|flags|fonts|publicdomain|favicon\\.ico|.*\\..*).*)" ],
};
