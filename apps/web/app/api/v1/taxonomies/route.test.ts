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
  suggestIndustries: vi.fn(),
  getAllSeniorities: vi.fn(),
  getAllOccupationsGrouped: vi.fn(),
  getAllTechnologiesGrouped: vi.fn(),
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

vi.mock("@/lib/actions/company", () => ({
  suggestIndustries: vi.fn(() => {
    throw new Error("taxonomies route must not import company server actions");
  }),
}));
vi.mock("@/lib/services/company", () => ({
  suggestIndustries: mocks.suggestIndustries,
}));
vi.mock("@/lib/services/taxonomy", () => ({
  getAllSeniorities: mocks.getAllSeniorities,
  getAllOccupationsGrouped: mocks.getAllOccupationsGrouped,
  getAllTechnologiesGrouped: mocks.getAllTechnologiesGrouped,
}));

import { GET } from "./route";

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/taxonomies${qs}`);
}

describe("GET /api/v1/taxonomies industries service boundary (#3331)", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.suggestIndustries.mockResolvedValue([]);
    mocks.getAllSeniorities.mockResolvedValue([]);
    mocks.getAllOccupationsGrouped.mockResolvedValue([]);
    mocks.getAllTechnologiesGrouped.mockResolvedValue([]);
  });

  it("returns 400 when the required `type` param is missing (#3213)", async () => {
    const res = await GET(makeReq("?locale=en"));
    const body = (await res.json()) as { error?: string };

    expect(res.status).toBe(400);
    expect(body.error).toMatch(/Missing or invalid 'type' param/);
    expect(mocks.suggestIndustries).not.toHaveBeenCalled();
    expect(mocks.getAllSeniorities).not.toHaveBeenCalled();
    expect(mocks.getAllOccupationsGrouped).not.toHaveBeenCalled();
    expect(mocks.getAllTechnologiesGrouped).not.toHaveBeenCalled();
  });

  it("rejects unsupported locales before loading taxonomy values", async () => {
    const res = await GET(makeReq("?type=industries&locale=xx"));
    const body = (await res.json()) as { error?: string };

    expect(res.status).toBe(400);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(body.error).toBe("Invalid 'locale' param. Supported: en, de, fr, it");
    expect(mocks.suggestIndustries).not.toHaveBeenCalled();
    expect(mocks.getAllSeniorities).not.toHaveBeenCalled();
    expect(mocks.getAllOccupationsGrouped).not.toHaveBeenCalled();
    expect(mocks.getAllTechnologiesGrouped).not.toHaveBeenCalled();
  });

  it("resolves industries through the company service tier", async () => {
    mocks.suggestIndustries.mockResolvedValue([
      { id: 3, name: "Technology" },
      { id: 42, name: "Financial Services" },
    ]);

    const res = await GET(makeReq("?type=industries&locale=de"));
    const body = (await res.json()) as {
      type?: string;
      items?: Array<{ id: number; name: string }>;
    };

    expect(res.status).toBe(200);
    expect(mocks.suggestIndustries).toHaveBeenCalledWith({
      query: "",
      locale: "de",
      failOnUnavailable: true,
    });
    expect(body).toEqual({
      type: "industries",
      items: [
        { id: 3, name: "Technology" },
        { id: 42, name: "Financial Services" },
      ],
    });
  });

  it("returns a non-cacheable safe error when a taxonomy provider fails", async () => {
    const providerError = new Error("provider-internal-canary-do-not-expose");
    mocks.suggestIndustries.mockRejectedValue(providerError);

    const res = await GET(makeReq("?type=industries&locale=en"));
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body).toEqual({ error: "Search service unavailable" });
    expect(JSON.stringify(body)).not.toContain("provider-internal-canary");
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(res.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      { service: "typesense", operation: "public_api_taxonomies" },
      providerError,
    );
  });

  it.each([
    ["seniority", "getAllSeniorities", ["en", undefined, { failOnUnavailable: true }]],
    ["occupations", "getAllOccupationsGrouped", ["en", undefined, { failOnUnavailable: true }]],
    ["technologies", "getAllTechnologiesGrouped", [undefined, { failOnUnavailable: true }]],
  ] as const)("opts %s into strict provider handling", async (type, mockName, args) => {
    const res = await GET(makeReq(`?type=${type}&locale=en`));

    expect(res.status).toBe(200);
    expect(mocks[mockName]).toHaveBeenCalledWith(...args);
  });
});
