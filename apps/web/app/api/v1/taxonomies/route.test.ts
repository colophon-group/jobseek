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
  suggestIndustries: vi.fn(),
  getAllSeniorities: vi.fn(),
  getAllOccupationsGrouped: vi.fn(),
  getAllTechnologiesGrouped: vi.fn(),
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
    vi.clearAllMocks();
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
    });
    expect(body).toEqual({
      type: "industries",
      items: [
        { id: 3, name: "Technology" },
        { id: 42, name: "Financial Services" },
      ],
    });
  });
});
