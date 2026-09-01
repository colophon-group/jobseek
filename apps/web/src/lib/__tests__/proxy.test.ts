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
import {
  createServer,
  request as sendHttpRequest,
} from "node:http";
import type { AddressInfo } from "node:net";
import { parse } from "csv-parse/sync";
import { readFileSync } from "node:fs";

const resourceStatusMocks = vi.hoisted(() => ({
  hasPublicCompanyRoute: vi.fn(),
  hasPublicWatchlistRoute: vi.fn(),
  hasWatchlistRouteForViewer: vi.fn(),
}));
const authMocks = vi.hoisted(() => ({ getSession: vi.fn() }));
const rateLimitMocks = vi.hoisted(() => ({
  burst: vi.fn(),
  sustained: vi.fn(),
  getClientIp: vi.fn(),
}));

vi.mock("@/lib/services/public-resource-status", () => resourceStatusMocks);
vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: authMocks.getSession } },
}));
vi.mock("@/lib/rate-limit", () => ({
  getClientIp: rateLimitMocks.getClientIp,
  publicReadBurstLimiter: { limit: rateLimitMocks.burst },
  publicReadSustainedLimiter: { limit: rateLimitMocks.sustained },
}));

import { proxy, config } from "../../../proxy";
import { staticMissingResourceDocument } from "@/lib/missing-resource-recovery";
import { RESERVED_USERNAMES } from "@/lib/username";

const companyRegistry = parse(
  readFileSync("../crawler/data/companies.csv", "utf8"),
  { columns: true, skip_empty_lines: true },
) as Array<{ slug: string }>;

const redirectSpy = vi.spyOn(NextResponse, "redirect");

beforeEach(() => {
  rateLimitMocks.burst.mockReset().mockResolvedValue({
    success: true,
    limit: 30,
    remaining: 29,
    reset: Date.now() + 60_000,
  });
  rateLimitMocks.sustained.mockReset().mockResolvedValue({
    success: true,
    limit: 300,
    remaining: 299,
    reset: Date.now() + 3_600_000,
  });
  rateLimitMocks.getClientIp.mockReset().mockReturnValue("203.0.113.7");
});

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

  it("passes the configured IndexNow proof path through to its rewrite", async () => {
    vi.stubEnv("INDEXNOW_KEY", "indexnow-verification-token");

    const response = await proxy(
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

  it("sets Cache-Control + Vary on Accept-Language redirects", async () => {
    const response = await proxy(createRequest("/about", "de-DE,de;q=0.9"));
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
    expect(response.headers.get("vary")).toBe("Accept-Language");
  });

  it("sets cache headers on the default-locale fallback redirect", async () => {
    const response = await proxy(createRequest("/"));
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
  });

  it("does NOT cache when a NEXT_LOCALE cookie is present", async () => {
    const response = await proxy(createRequest("/about", undefined, "fr"));
    expect(response.headers.get("cache-control")).toBeNull();
    expect(response.headers.get("vary")).toBeNull();
  });

  it("ignores an invalid cookie locale and still caches", async () => {
    const response = await proxy(createRequest("/about", "de-DE,de;q=0.9", "xx"));
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=86400, s-maxage=86400",
    );
  });
});

describe("company request auth boundary", () => {
  it("redirects anonymous visitors before the app shell and preserves prefills", async () => {
    const response = await proxy(
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

  it("lets hinted visitors reach the server-verified request page", async () => {
    const response = await proxy(
      createRequest("/en/companies/request?name=Acme", undefined, undefined, true),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("missing-resource HTTP boundary", () => {
  const forbiddenFetch = vi.fn();

  beforeEach(() => {
    resourceStatusMocks.hasPublicCompanyRoute.mockReset();
    resourceStatusMocks.hasPublicWatchlistRoute.mockReset();
    resourceStatusMocks.hasWatchlistRouteForViewer.mockReset();
    authMocks.getSession.mockReset().mockResolvedValue(null);
    forbiddenFetch.mockReset().mockResolvedValue(new Response(
      '<html><head><link rel="preload" as="script" href="/leak.js"></head><body><scr<script>ipt>alert(1)</script\t\n bar></body></html>',
    ));
    vi.stubGlobal("fetch", forbiddenFetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function documentRequest(pathname: string): NextRequest {
    return new NextRequest(`http://localhost${pathname}`, {
      headers: { accept: "text/html,application/xhtml+xml" },
    });
  }

  async function requestThroughHttp(
    pathname: string,
    headers: Record<string, string> = {},
  ): Promise<{ status: number; body: string; headers: Record<string, string | string[] | undefined> }> {
    const server = createServer(async (request, response) => {
      const requestHeaders: Record<string, string> = {};
      for (const [name, value] of Object.entries(request.headers)) {
        if (Array.isArray(value)) {
          requestHeaders[name] = value.join(", ");
        } else if (value) {
          requestHeaders[name] = value;
        }
      }
      const nextRequest = new NextRequest(`http://localhost${request.url}`, {
        method: request.method,
        headers: requestHeaders,
      });
      // happy-dom's Headers implementation strips Cookie as a forbidden
      // browser header. Rehydrate the server-side cookie jar from the actual
      // Node HTTP request so this adapter matches Next's production request.
      for (const pair of (request.headers.cookie ?? "").split(";")) {
        const separator = pair.indexOf("=");
        if (separator > 0) {
          nextRequest.cookies.set(
            pair.slice(0, separator).trim(),
            pair.slice(separator + 1).trim(),
          );
        }
      }
      const proxyResponse = await proxy(nextRequest);
      response.writeHead(
        proxyResponse.status,
        Object.fromEntries(proxyResponse.headers.entries()),
      );
      response.end(Buffer.from(await proxyResponse.arrayBuffer()));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address() as AddressInfo;
    try {
      return await new Promise<{ status: number; body: string; headers: Record<string, string | string[] | undefined> }>((resolve, reject) => {
        const request = sendHttpRequest({
          hostname: "127.0.0.1",
          port,
          path: pathname,
          method: "GET",
          headers: { accept: "text/html", ...headers },
        }, (response) => {
          const chunks: Buffer[] = [];
          response.on("data", (chunk: Buffer) => chunks.push(chunk));
          response.on("end", () => resolve({
            status: response.statusCode ?? 0,
            body: Buffer.concat(chunks).toString("utf8"),
            headers: response.headers,
          }));
        });
        request.on("error", reject);
        request.end();
      });
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
  }

  it("rewrites a definitively missing company to localized UI with HTTP 404", async () => {
    resourceStatusMocks.hasPublicCompanyRoute.mockResolvedValue(false);

    const response = await proxy(
      documentRequest("/de/company/definitely-missing"),
    );

    expect(response.status).toBe(404);
    expect(response.headers.get("x-middleware-rewrite")).toBeNull();
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("x-robots-tag")).toBe("noindex, follow");
    expect(response.headers.get("content-language")).toBe("de");
    expect(response.headers.get("content-security-policy")).toContain(
      "default-src 'none'; script-src 'none'",
    );
    expect(response.headers.get("link")).toBeNull();
    const body = await response.text();
    expect(body).toContain("<h1>Unternehmen nicht gefunden</h1>");
    expect(body).toContain('href="/de/explore"');
    expect(body).toContain(
      'href="/de/companies/request?name=definitely+missing"',
    );
    expect(body).not.toContain("<script");
    expect(body).not.toContain('as="script"');
    expect(forbiddenFetch).not.toHaveBeenCalled();
  });

  it("never interpolates hostile slug markup or relies on regex filtering", () => {
    const body = staticMissingResourceDocument(
      "company",
      "en",
      'acme\"><scr<script>ipt>alert(1)</script\t\n bar>',
    );

    expect(body).not.toContain("<scr<script>ipt>");
    expect(body).not.toContain("</script\t\n bar>");
    expect(body).not.toContain("<script");
    expect(body).not.toContain('as="script"');
    expect(body).toContain("name=acme%22%3E%3Cscr%3Cscript%3Eipt%3Ealert%281%29");
  });

  it("returns the same script-free 404 headers and no body for HEAD", async () => {
    resourceStatusMocks.hasPublicCompanyRoute.mockResolvedValue(false);
    const response = await proxy(new NextRequest(
      "http://localhost/fr/company/definitely-missing",
      { method: "HEAD" },
    ));

    expect(response.status).toBe(404);
    expect(response.headers.get("content-language")).toBe("fr");
    expect(response.headers.get("content-security-policy")).toContain(
      "script-src 'none'",
    );
    expect(await response.text()).toBe("");
    expect(forbiddenFetch).not.toHaveBeenCalled();
  });

  it("passes a company recognized after deployment without a matcher update", async () => {
    resourceStatusMocks.hasPublicCompanyRoute.mockResolvedValue(true);

    const response = await proxy(
      documentRequest("/en/company/newly-synced-company"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("fails open when company status cannot be established", async () => {
    resourceStatusMocks.hasPublicCompanyRoute.mockRejectedValue(
      new Error("unavailable"),
    );
    const log = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await proxy(documentRequest("/en/company/acme"));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
    log.mockRestore();
  });

  it("gives absent, private, and grandfathered-public anonymous watchlists the same 404", async () => {
    const absent = await proxy(documentRequest("/en/ghost/missing"));
    const privateResponse = await proxy(
      documentRequest("/en/alice/private-list"),
    );
    const grandfatheredPublic = await proxy(
      documentRequest("/en/alice/still-public"),
    );

    for (const response of [absent, privateResponse, grandfatheredPublic]) {
      expect(response.status).toBe(404);
      expect(response.headers.get("x-middleware-rewrite")).toBeNull();
      expect(response.headers.get("cache-control")).toBe("private, no-store");
      expect(response.headers.get("x-robots-tag")).toBe("noindex, follow");
    }
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
  });

  it("hard-404s syntactically impossible non-reserved watchlist paths", async () => {
    const response = await proxy(documentRequest("/en/alice/private.list"));

    expect(response.status).toBe(404);
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
    expect(authMocks.getSession).not.toHaveBeenCalled();
  });

  it("does not preserve anonymous access to a grandfathered public watchlist", async () => {
    const response = await proxy(documentRequest("/fr/alice/public-list"));

    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
  });

  it("preserves a private route for its verified owner", async () => {
    const request = documentRequest("/it/alice/private-list");
    request.cookies.set("__Secure-better-auth.session_token", "opaque-session");
    authMocks.getSession.mockResolvedValue({ user: { id: "owner-1" } });
    resourceStatusMocks.hasWatchlistRouteForViewer.mockResolvedValue(true);

    const response = await proxy(request);

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
    expect(resourceStatusMocks.hasWatchlistRouteForViewer).toHaveBeenCalledWith(
      "alice",
      "private-list",
      "owner-1",
    );
  });

  it("treats a stale or forged session cookie as anonymous", async () => {
    const request = documentRequest("/en/alice/private-list");
    request.cookies.set("better-auth.session_token", "stale-session");
    authMocks.getSession.mockResolvedValue(null);
    const response = await proxy(request);

    expect(response.status).toBe(404);
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
    expect(resourceStatusMocks.hasWatchlistRouteForViewer).not.toHaveBeenCalled();
  });

  it("does not disclose a private route to a verified non-owner", async () => {
    const request = documentRequest("/en/alice/private-list");
    request.cookies.set("better-auth.session_token", "other-session");
    authMocks.getSession.mockResolvedValue({ user: { id: "other-user" } });
    resourceStatusMocks.hasWatchlistRouteForViewer.mockResolvedValue(false);

    const response = await proxy(request);

    expect(response.status).toBe(404);
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
  });

  it("makes public, private, and missing rows HTTP-indistinguishable to a verified non-owner", async () => {
    authMocks.getSession.mockResolvedValue({ user: { id: "other-user" } });
    resourceStatusMocks.hasWatchlistRouteForViewer.mockResolvedValue(false);

    const responses = await Promise.all([
      requestThroughHttp("/en/alice/still-public", {
        cookie: "better-auth.session_token=other-session",
      }),
      requestThroughHttp("/en/alice/private-list", {
        cookie: "better-auth.session_token=other-session",
      }),
      requestThroughHttp("/en/alice/missing", {
        cookie: "better-auth.session_token=other-session",
      }),
    ]);

    for (const response of responses) {
      expect(response.status).toBe(404);
      expect(response.headers["cache-control"]).toBe("private, no-store");
      expect(response.headers["x-robots-tag"]).toBe("noindex, follow");
      expect(response.headers["content-security-policy"]).toContain("default-src 'none'");
      expect(response.body).toContain("<h1>Watchlist not found</h1>");
    }
    expect(new Set(responses.map((response) => response.body)).size).toBe(1);
  });

  it("preserves anonymous-private 404 and verified-owner 200 over HTTP", async () => {
    resourceStatusMocks.hasPublicWatchlistRoute.mockResolvedValue(false);
    const anonymous = await requestThroughHttp("/en/alice/private-list");
    expect(anonymous.status).toBe(404);
    expect(anonymous.body).toContain("<h1>Watchlist not found</h1>");

    authMocks.getSession.mockResolvedValue({ user: { id: "owner-1" } });
    resourceStatusMocks.hasWatchlistRouteForViewer.mockResolvedValue(true);
    const owner = await requestThroughHttp("/en/alice/private-list", {
      cookie: "better-auth.session_token=valid-owner-session",
    });
    expect(authMocks.getSession).toHaveBeenCalled();
    expect(resourceStatusMocks.hasWatchlistRouteForViewer).toHaveBeenCalledWith(
      "alice",
      "private-list",
      "owner-1",
    );
    expect(owner.status).toBe(200);
  });

  it("does not add existence lookups to RSC navigations", async () => {
    const response = await proxy(
      new NextRequest("http://localhost/en/company/acme?_rsc=abc", {
        headers: { accept: "text/x-component", rsc: "1" },
      }),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(resourceStatusMocks.hasPublicCompanyRoute).not.toHaveBeenCalled();
  });
});

describe("Explore PPR shell normalization", () => {
  const obsoleteActionId = "7ffac6a500b0410a78dcf5f6a75ea0d2253b635222";

  it("rewrites query-bearing HTML documents to the locale shell", async () => {
    const request = new NextRequest(
      "http://localhost/en/explore?q=python&wm=remote",
      { headers: { accept: "text/html,application/xhtml+xml" } },
    );

    const response = await proxy(request);

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-rewrite")).toBe(
      "http://localhost/en/explore",
    );
    expect(request.nextUrl.search).toBe("?q=python&wm=remote");
  });

  it("does not rewrite queryless Explore documents", async () => {
    const response = await proxy(
      new NextRequest("http://localhost/en/explore", {
        headers: { accept: "text/html" },
      }),
    );

    expect(response.headers.get("x-middleware-rewrite")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("preserves RSC navigation queries", async () => {
    const response = await proxy(
      new NextRequest("http://localhost/en/explore?q=python&_rsc=abc123", {
        headers: { accept: "text/x-component", rsc: "1" },
      }),
    );

    expect(response.headers.get("x-middleware-rewrite")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("preserves Server Action queries", async () => {
    const response = await proxy(
      new NextRequest("http://localhost/en/explore?q=python", {
        method: "POST",
        headers: { accept: "text/x-component", "next-action": "action-id" },
      }),
    );

    expect(response.headers.get("x-middleware-rewrite")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(rateLimitMocks.burst).toHaveBeenCalledWith("203.0.113.7");
    expect(rateLimitMocks.sustained).toHaveBeenCalledWith("203.0.113.7");
  });

  it.each(["en", "de", "fr", "it"])(
    "rejects the confirmed obsolete %s Explore action before the page Function",
    async (locale) => {
      const response = await proxy(
        new NextRequest(`http://localhost/${locale}/explore?q=python`, {
          method: "POST",
          headers: {
            accept: "text/x-component",
            "next-action": obsoleteActionId,
          },
        }),
      );

      expect(response.status).toBe(404);
      await expect(response.text()).resolves.toBe("Not Found");
      expect(response.headers.get("cache-control")).toBe("private, no-store");
      expect(response.headers.get("x-middleware-next")).toBeNull();
    },
  );
});

describe("public browsing Server Action rate limit", () => {
  it.each([
    ["explore", "/en/explore"],
    ["company", "/de/company/acme"],
    ["watchlists", "/fr/watchlists"],
  ])("checks both windows for %s actions", async (_surface, pathname) => {
    resourceStatusMocks.hasPublicWatchlistRoute.mockResolvedValue(true);
    const response = await proxy(new NextRequest(`http://localhost${pathname}`, {
      method: "POST",
      headers: {
        "next-action": "current-deployment-action-id",
        "x-forwarded-for": "spoofed, 203.0.113.7",
      },
    }));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(rateLimitMocks.burst).toHaveBeenCalledWith("203.0.113.7");
    expect(rateLimitMocks.sustained).toHaveBeenCalledWith("203.0.113.7");
  });

  it("hard-404s every retired legacy watchlist action without an existence lookup", async () => {
    const response = await proxy(new NextRequest(
      "http://localhost/en/alice/missing-list",
      {
        method: "POST",
        headers: { "next-action": "stale-watchlist-action-id" },
      },
    ));

    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("x-robots-tag")).toBe("noindex, follow");
    expect(response.headers.get("x-middleware-next")).toBeNull();
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
    expect(resourceStatusMocks.hasWatchlistRouteForViewer).not.toHaveBeenCalled();
  });

  it("does not revive a legacy action merely because its row once existed", async () => {
    const response = await proxy(new NextRequest(
      "http://localhost/en/alice/backend-jobs",
      {
        method: "POST",
        headers: { "next-action": "current-watchlist-action-id" },
      },
    ));

    expect(response.status).toBe(404);
    expect(response.headers.get("x-middleware-next")).toBeNull();
  });

  it("does not call Postgres for a retired legacy action", async () => {
    const response = await proxy(new NextRequest(
      "http://localhost/en/alice/backend-jobs",
      {
        method: "POST",
        headers: { "next-action": "current-watchlist-action-id" },
      },
    ));

    expect(response.status).toBe(404);
    expect(resourceStatusMocks.hasPublicWatchlistRoute).not.toHaveBeenCalled();
    expect(resourceStatusMocks.hasWatchlistRouteForViewer).not.toHaveBeenCalled();
  });

  it("returns a non-cacheable 429 before the page action when either window is exhausted", async () => {
    const reset = Date.now() + 45_000;
    rateLimitMocks.burst.mockResolvedValue({
      success: false,
      limit: 30,
      remaining: 0,
      reset,
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const response = await proxy(new NextRequest("http://localhost/en/company/acme", {
      method: "POST",
      headers: { "next-action": "current-deployment-action-id" },
    }));

    expect(response.status).toBe(429);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(Number(response.headers.get("retry-after"))).toBeGreaterThan(0);
    expect(response.headers.get("x-middleware-next")).toBeNull();
    await expect(response.text()).resolves.toBe("Too Many Requests");
    expect(warn).toHaveBeenCalledWith(expect.stringContaining(
      '"event":"public_read.rate_limited"',
    ));
    expect(warn.mock.calls.flat().join(" ")).not.toContain("203.0.113.7");
    warn.mockRestore();
  });

  it("fails open on Redis transport errors and logs only a hashed client reference", async () => {
    rateLimitMocks.burst.mockRejectedValue(new Error("secret transport detail"));
    const error = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await proxy(new NextRequest("http://localhost/en/watchlists", {
      method: "POST",
      headers: { "next-action": "current-deployment-action-id" },
    }));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
    const logged = error.mock.calls.flat().join(" ");
    expect(logged).toContain('"event":"public_read.rate_limit_unavailable"');
    expect(logged).not.toContain("203.0.113.7");
    expect(logged).not.toContain("secret transport detail");
    error.mockRestore();
  });

  it("does not rate-limit ordinary documents or reserved application routes", async () => {
    await proxy(new NextRequest("http://localhost/en/explore", {
      headers: { accept: "text/html" },
    }));
    await proxy(new NextRequest("http://localhost/en/settings/account", {
      method: "POST",
      headers: { "next-action": "settings-action" },
    }));

    expect(rateLimitMocks.burst).not.toHaveBeenCalled();
    expect(rateLimitMocks.sustained).not.toHaveBeenCalled();
  });
});

describe("scanner path boundary", () => {
  it.each([
    "/en/wp-admin/install.php",
    "/de/phpmyadmin/index.php",
    "/fr/.git/config",
    "/it/alice/.env",
  ])("returns a cacheable 404 for %s", async (pathname) => {
    const response = await proxy(createRequest(pathname));

    expect(response.status).toBe(404);
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

  it("matches localized proxy boundaries", () => {
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
        url: "/en/explore?q=python",
        headers: { accept: "text/html,application/xhtml+xml" },
      }),
    ).toBe(true);
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/company/amazon",
      }),
    ).toBe(false);
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/company/definitely-not-real",
      }),
    ).toBe(true);
  });

  it("routes post-deployment company additions through the status boundary", () => {
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/company/newly-synced-company",
      }),
    ).toBe(true);
  });

  it("bypasses Proxy for every canonical company in the registry", () => {
    for (const { slug } of companyRegistry) {
      expect(
        unstable_doesMiddlewareMatch({
          config,
          nextConfig: {},
          url: `/en/company/${slug}`,
        }),
        slug,
      ).toBe(false);
    }
  }, 60_000);

  it("bypasses the watchlist boundary for every reserved application prefix", () => {
    const watchlistOnlyConfig = { matcher: [config.matcher.at(-1)!] };
    for (const userSlug of RESERVED_USERNAMES) {
      expect(
        unstable_doesMiddlewareMatch({
          config: watchlistOnlyConfig,
          nextConfig: {},
          url: `/en/${userSlug}/stats`,
        }),
        userSlug,
      ).toBe(false);
    }
  });

  it.each([
    "/en/my-jobs/stats",
    "/de/settings/account",
    "/fr/settings/billing",
    "/it/blog/example-post",
    "/en/companies/example",
  ])("bypasses Proxy for reserved multi-segment app route %s", (url) => {
    expect(
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url }),
    ).toBe(false);
  });

  it("keeps ordinary watchlist documents on the status boundary", () => {
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/alice/jobs",
      }),
    ).toBe(true);
  });

  it("excludes Explore RSC but matches current Server Actions at the proxy boundary", () => {
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/explore?q=python&_rsc=abc123",
        headers: { accept: "text/x-component", rsc: "1" },
      }),
    ).toBe(false);
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url: "/en/explore?q=python",
        headers: {
          accept: "text/x-component",
          "next-action": "action-id",
        },
      }),
    ).toBe(true);
  });

  it.each(["en", "de", "fr", "it"])(
    "matches the confirmed obsolete %s Explore action at the proxy boundary",
    (locale) => {
      expect(
        unstable_doesMiddlewareMatch({
          config,
          nextConfig: {},
          url: `/${locale}/explore?q=python`,
          headers: {
            accept: "text/x-component",
            "next-action": "7ffac6a500b0410a78dcf5f6a75ea0d2253b635222",
          },
        }),
      ).toBe(true);
    },
  );

  it.each([
    "/en/explore",
    "/de/watchlists",
    "/fr/company/amazon",
    "/it/alice/backend-jobs",
  ])("matches protected Server Action surface %s", (url) => {
    expect(
      unstable_doesMiddlewareMatch({
        config,
        nextConfig: {},
        url,
        headers: { "next-action": "current-deployment-action-id" },
      }),
    ).toBe(true);
  });

  it.each([
    "/en/wp-admin/install.php",
    "/de/phpmyadmin/index.php",
    "/fr/.git/config",
    "/it/alice/.env",
  ])("matches scanner path %s at the proxy boundary", (url) => {
    expect(
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url }),
    ).toBe(true);
  });

  it.each([
    "/en/alice/jobs",
    "/de/bob/acme",
  ])("matches localized two-segment resource candidate %s", (url) => {
    expect(
      unstable_doesMiddlewareMatch({ config, nextConfig: {}, url }),
    ).toBe(true);
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
