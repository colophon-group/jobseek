import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
  afterAll,
} from "vitest";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest, NextResponse } from "next/server";
import { proxy, config } from "../../../proxy";

const redirectSpy = vi.spyOn(NextResponse, "redirect");

function createRequest(
  pathname: string,
  acceptLanguage?: string,
  cookieLocale?: string,
  loggedIn = false,
): NextRequest {
  const url = new URL(`http://localhost${pathname}`);
  const headers: Record<string, string> = {};
  if (acceptLanguage) headers["accept-language"] = acceptLanguage;

  const request = new NextRequest(url, { headers });
  if (cookieLocale) request.cookies.set("NEXT_LOCALE", cookieLocale);
  if (loggedIn) request.cookies.set("logged_in", "1");
  return request;
}

function redirectedPathname(): string {
  expect(redirectSpy).toHaveBeenCalled();
  const [target] = redirectSpy.mock.calls.at(-1)!;
  return new URL(target.toString()).pathname;
}

describe("proxy", () => {
  beforeEach(() => {
    redirectSpy.mockClear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  afterAll(() => {
    redirectSpy.mockRestore();
  });

  it("redirects to default locale when no accept-language", () => {
    proxy(createRequest("/about"));
    expect(redirectSpy).toHaveBeenCalledTimes(1);
    expect(redirectedPathname()).toBe("/en/about");
  });

  it("redirects to German when de is preferred", () => {
    proxy(createRequest("/about", "de-DE,de;q=0.9,en;q=0.8"));
    expect(redirectedPathname()).toBe("/de/about");
  });

  it("redirects to French when fr is preferred", () => {
    proxy(createRequest("/pricing", "fr-FR,fr;q=0.9"));
    expect(redirectedPathname()).toBe("/fr/pricing");
  });

  it("falls back to default locale for unsupported languages", () => {
    proxy(createRequest("/about", "ja,zh;q=0.9"));
    expect(redirectedPathname()).toBe("/en/about");
  });

  it("respects quality weights", () => {
    proxy(createRequest("/", "en;q=0.5,it;q=0.9"));
    expect(redirectedPathname()).toBe("/it");
  });

  it("handles root path", () => {
    proxy(createRequest("/"));
    expect(redirectedPathname()).toBe("/en");
  });

  it("preserves an unknown dotted path when adding the locale", () => {
    proxy(createRequest("/does-not-exist.png"));
    expect(redirectedPathname()).toBe("/en/does-not-exist.png");
  });

  it("passes the configured IndexNow proof path through to its rewrite", () => {
    vi.stubEnv("INDEXNOW_KEY", "indexnow-verification-token");

    const response = proxy(
      createRequest("/indexnow-verification-token.txt"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("uses the cookie locale when present", () => {
    proxy(createRequest("/about", "de-DE,de;q=0.9", "fr"));
    expect(redirectedPathname()).toBe("/fr/about");
  });
});

describe("proxy caching", () => {
  beforeEach(() => {
    redirectSpy.mockClear();
  });

  it("sets Cache-Control + Vary on Accept-Language redirects", () => {
    const response = proxy(createRequest("/about", "de-DE,de;q=0.9"));
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
    expect(response.headers.get("vary")).toBe("Accept-Language");
  });

  it("sets cache headers on the default-locale fallback redirect", () => {
    const response = proxy(createRequest("/"));
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
  });

  it("does NOT cache when a NEXT_LOCALE cookie is present", () => {
    const response = proxy(createRequest("/about", undefined, "fr"));
    expect(response.headers.get("cache-control")).toBeNull();
    expect(response.headers.get("vary")).toBeNull();
  });

  it("ignores an invalid cookie locale and still caches", () => {
    const response = proxy(createRequest("/about", "de-DE,de;q=0.9", "xx"));
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
  });
});

describe("company request auth boundary", () => {
  it("redirects anonymous visitors before the app shell and preserves prefills", () => {
    const response = proxy(
      createRequest(
        "/de/companies/request?name=MissingCo&website=https%3A%2F%2Fexample.com%2Fcareers",
      ),
    );

    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location")!);
    expect(location.pathname).toBe("/de/sign-in");
    expect(location.searchParams.get("next")).toBe(
      "/de/companies/request?name=MissingCo&website=https%3A%2F%2Fexample.com%2Fcareers",
    );
  });

  it("lets hinted visitors reach the server-verified request page", () => {
    const response = proxy(
      createRequest("/en/companies/request?name=Acme", undefined, undefined, true),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("proxy config", () => {
  it("has a matcher pattern", () => {
    expect(config.matcher).toBeDefined();
    expect(config.matcher.length).toBeGreaterThan(0);
  });

  it("matches the localized company-request auth boundary only", () => {
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/companies/request?name=Acme",
      }),
    ).toBe(true);
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/explore",
      }),
    ).toBe(false);
  });

  it.each([
    "/does-not-exist.png",
    "/robots-nope.txt",
    "/random.html",
    "/favicon-missing.png",
    "/apple-touch-icon-missing.jpg",
    "/logo-missing.svg",
    "/indexnow-verification-token.txt",
  ])("matches unknown dotted root path %s", (url) => {
    expect(
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url }),
    ).toBe(true);
  });

  it.each([
    "/apple-touch-icon.png",
    "/apple-touch-icon-120x120.png",
    "/favicon-32x32.png",
    "/android-chrome-192x192.png",
    "/site.webmanifest",
    "/BingSiteAuth.xml",
    "/.well-known/ai-plugin.json",
    "/openapi.json",
    "/llms.txt",
    "/robots.txt",
    "/sitemap.xml",
    "/flags/ch.svg",
    "/fonts/JetBrainsMono-Regular.woff2",
    "/screenshots/en/feature1-dark.png",
  ])("bypasses known static or discovery route %s", (url) => {
    expect(
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url }),
    ).toBe(false);
  });
});
