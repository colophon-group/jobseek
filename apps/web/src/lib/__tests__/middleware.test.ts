import { describe, it, expect, vi, beforeEach } from "vitest";

const mockRedirect = vi.fn();

vi.mock("next/server", () => {
  class MockNextResponse {
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

function createMockRequest(pathname: string, acceptLanguage?: string): NextRequest {
  const url = new URL(`http://localhost${pathname}`);
  const headersMap = new Map<string, string>();
  if (acceptLanguage) headersMap.set("accept-language", acceptLanguage);

  return {
    headers: {
      get: (name: string) => headersMap.get(name) ?? null,
      forEach: (cb: (value: string, key: string) => void) => headersMap.forEach(cb),
    },
    cookies: {
      get: (_name: string) => undefined,
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
});

describe("middleware config", () => {
  it("has a matcher pattern", () => {
    expect(config.matcher).toBeDefined();
    expect(config.matcher.length).toBeGreaterThan(0);
  });
});
