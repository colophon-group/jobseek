import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: {
    limit: vi.fn(async () => {
      throw new Error("no redis in unit tests");
    }),
  },
  getClientIp: () => "test-ip",
}));

const mocks = vi.hoisted(() => ({
  logExternalError: vi.fn(),
  suggestCompanies: vi.fn(),
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

vi.mock("@/lib/actions/company", () => ({
  suggestCompanies: vi.fn(() => {
    throw new Error("companies route must not import company server actions");
  }),
}));
vi.mock("@/lib/services/company", () => ({
  suggestCompanies: mocks.suggestCompanies,
}));

import { GET } from "./route";

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/companies${qs}`);
}

describe("GET /api/v1/companies service boundary (#3331)", () => {
  beforeEach(() => {
    mocks.logExternalError.mockReset();
    mocks.suggestCompanies.mockReset();
  });

  it("returns 400 when the required `q` param is missing (#3213)", async () => {
    const res = await GET(makeReq("?locale=en"));
    const body = (await res.json()) as { error?: string };

    expect(res.status).toBe(400);
    expect(body.error).toMatch(/Missing required 'q' param/);
    expect(mocks.suggestCompanies).not.toHaveBeenCalled();
  });

  it("rejects unsupported locales before constructing company URLs", async () => {
    const res = await GET(makeReq("?q=goo&locale=xx"));
    const body = (await res.json()) as { error?: string };

    expect(res.status).toBe(400);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(body.error).toBe("Invalid 'locale' param. Supported: en, de, fr, it");
    expect(mocks.suggestCompanies).not.toHaveBeenCalled();
  });

  it("resolves company suggestions through the service tier", async () => {
    mocks.suggestCompanies.mockResolvedValue([
      {
        id: "co-1",
        name: "Google",
        slug: "google",
        icon: "https://cdn.example/google.png",
      },
    ]);

    const res = await GET(makeReq("?q=goo&locale=de"));
    const body = (await res.json()) as {
      companies?: Array<{
        name: string;
        slug: string;
        icon: string | null;
        url: string;
      }>;
    };

    expect(res.status).toBe(200);
    expect(mocks.suggestCompanies).toHaveBeenCalledWith({
      query: "goo",
      failOnUnavailable: true,
    });
    expect(body.companies).toEqual([
      {
        name: "Google",
        slug: "google",
        icon: "https://cdn.example/google.png",
        url: "https://jseek.co/de/company/google",
      },
    ]);
  });

  it("returns a non-cacheable safe error when Typesense is unavailable", async () => {
    const providerError = new Error("provider-internal-canary-do-not-expose");
    mocks.suggestCompanies.mockRejectedValue(providerError);

    const res = await GET(makeReq("?q=goo&locale=en"));
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body).toEqual({ error: "Search service unavailable" });
    expect(JSON.stringify(body)).not.toContain("provider-internal-canary");
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      { service: "typesense", operation: "public_api_companies" },
      providerError,
    );
  });
});
