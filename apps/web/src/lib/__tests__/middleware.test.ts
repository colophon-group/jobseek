import { describe, it, expect, vi, beforeEach } from "vitest";

const mockRedirect = vi.fn();

// Headers-like wrapper around a plain Map. We don't need a full Headers
// implementation — the middleware only calls set(), and tests only need get().
class MockHeaders {
  private store = new Map<string, string>();
  set(name: string, value: string) {
    this.store.set(name.toLowerCase(), value);
  }
  get(name: string): string | null {
    return this.store.get(name.toLowerCase()) ?? null;
  }
}

vi.mock("next/server", () => {
  class MockNextResponse {
    headers = new MockHeaders();
    static redirect(url: URL) {
      mockRedirect(url);
      return new MockNextResponse();
    }
  }

  return {
    NextResponse: MockNextResponse,
    NextRequest: class {},
  };
});

import { middleware, config } from "../../../middleware";
import type { NextRequest } from "next/server";

function createMockRequest(
  pathname: string,
  acceptLanguage?: string,
  cookieLocale?: string,
): NextRequest {
  const url = new URL(`http://localhost${pathname}`);
  const headersMap = new Map<string, string>();
  if (acceptLanguage) headersMap.set("accept-language", acceptLanguage);

  return {
    headers: {
      get: (name: string) => headersMap.get(name) ?? null,
      forEach: (cb: (value: string, key: string) => void) => headersMap.forEach(cb),
    },
    cookies: {
      get: (name: string) =>
        name === "NEXT_LOCALE" && cookieLocale
          ? { value: cookieLocale }
          : undefined,
    },
    nextUrl: {
      clone: () => new URL(url),
      pathname,
    },
  } as unknown as NextRequest;
}

describe("middleware", () => {
  beforeEach(() => {
    mockRedirect.mockClear();
  });

  it("redirects to default locale when no accept-language", () => {
    middleware(createMockRequest("/about"));
    expect(mockRedirect).toHaveBeenCalledTimes(1);
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/en/about");
  });

  it("redirects to German when de is preferred", () => {
    middleware(createMockRequest("/about", "de-DE,de;q=0.9,en;q=0.8"));
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/de/about");
  });

  it("redirects to French when fr is preferred", () => {
    middleware(createMockRequest("/pricing", "fr-FR,fr;q=0.9"));
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/fr/pricing");
  });

  it("falls back to default locale for unsupported languages", () => {
    middleware(createMockRequest("/about", "ja,zh;q=0.9"));
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/en/about");
  });

  it("respects quality weights", () => {
    middleware(createMockRequest("/", "en;q=0.5,it;q=0.9"));
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/it/");
  });

  it("handles root path", () => {
    middleware(createMockRequest("/"));
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/en/");
  });

  it("uses the cookie locale when present", () => {
    middleware(createMockRequest("/about", "de-DE,de;q=0.9", "fr"));
    const redirectUrl = mockRedirect.mock.calls[0][0] as URL;
    expect(redirectUrl.pathname).toBe("/fr/about");
  });
});

describe("middleware caching", () => {
  beforeEach(() => {
    mockRedirect.mockClear();
  });

  it("sets Cache-Control + Vary on Accept-Language redirects", () => {
    const response = middleware(
      createMockRequest("/about", "de-DE,de;q=0.9"),
    ) as unknown as { headers: { get: (n: string) => string | null } };
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
    expect(response.headers.get("vary")).toBe("Accept-Language");
  });

  it("sets cache headers on the default-locale fallback redirect", () => {
    const response = middleware(createMockRequest("/")) as unknown as {
      headers: { get: (n: string) => string | null };
    };
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
  });

  it("does NOT cache when a NEXT_LOCALE cookie is present", () => {
    const response = middleware(
      createMockRequest("/about", undefined, "fr"),
    ) as unknown as { headers: { get: (n: string) => string | null } };
    expect(response.headers.get("cache-control")).toBeNull();
    expect(response.headers.get("vary")).toBeNull();
  });

  it("ignores an invalid cookie locale and still caches", () => {
    const response = middleware(
      createMockRequest("/about", "de-DE,de;q=0.9", "xx"),
    ) as unknown as { headers: { get: (n: string) => string | null } };
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
  });
});

describe("middleware config", () => {
  it("has a matcher pattern", () => {
    expect(config.matcher).toBeDefined();
    expect(config.matcher.length).toBeGreaterThan(0);
  });
});
