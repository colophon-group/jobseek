import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { NextRequest, NextResponse } from "next/server";
import { proxy, config } from "../../../proxy";

const redirectSpy = vi.spyOn(NextResponse, "redirect");

function createRequest(
  pathname: string,
  acceptLanguage?: string,
  cookieLocale?: string,
): NextRequest {
  const url = new URL(`http://localhost${pathname}`);
  const headers: Record<string, string> = {};
  if (acceptLanguage) headers["accept-language"] = acceptLanguage;

  const request = new NextRequest(url, { headers });
  if (cookieLocale) request.cookies.set("NEXT_LOCALE", cookieLocale);
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

describe("proxy config", () => {
  it("has a matcher pattern", () => {
    expect(config.matcher).toBeDefined();
    expect(config.matcher.length).toBeGreaterThan(0);
  });
});
