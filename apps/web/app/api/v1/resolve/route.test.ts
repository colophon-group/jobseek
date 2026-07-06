/**
 * Regression tests for `/api/v1/resolve` industry handling.
 *
 * Issue #3228: the `industries` case used to emit `slug: String(i.id)`,
 * which violated the response contract — the field is named `slug`,
 * documented as a URL-stable slug-shaped string (consistent with the
 * other taxonomies — locations, occupations, seniority, technologies).
 *
 * The fix derives the slug from the localized display name via the
 * canonical `slugifyTitle` helper (the same one watchlist titles use).
 * Industries have no `slug` column today; the `id` is retained only as
 * a fallback when the name slugifies to empty (unreachable for current
 * taxonomy values, defensive for pathological future inputs).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("server-only", () => ({}));

// Bypass real rate limiter — degrades closed when the limiter throws.
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
  suggestLocations: vi.fn(),
  suggestOccupations: vi.fn(),
  suggestSeniorities: vi.fn(),
  suggestTechnologies: vi.fn(),
}));

vi.mock("@/lib/actions/company", () => ({
  suggestIndustries: vi.fn(() => {
    throw new Error("route must not import company server actions");
  }),
}));
vi.mock("@/lib/services/company", () => ({
  suggestIndustries: mocks.suggestIndustries,
}));
vi.mock("@/lib/actions/locations", () => ({
  suggestLocations: vi.fn(() => {
    throw new Error("route must not import location server actions");
  }),
}));
vi.mock("@/lib/services/locations", () => ({
  suggestLocations: mocks.suggestLocations,
}));
// The route handler now imports taxonomy helpers from the service tier
// (see #3329). Mock that path so the substitution intercepts the call
// chain.
vi.mock("@/lib/services/taxonomy", () => ({
  suggestOccupations: mocks.suggestOccupations,
  suggestSeniorities: mocks.suggestSeniorities,
  suggestTechnologies: mocks.suggestTechnologies,
}));

import { GET } from "./route";

function makeReq(qs: string): NextRequest {
  return new NextRequest(`http://localhost/api/v1/resolve${qs}`);
}

async function call(qs: string) {
  const res = await GET(makeReq(qs));
  const body = (await res.json()) as {
    type?: string;
    query?: string;
    matches?: { slug: string; name: string }[];
    error?: string;
  };
  return { res, body };
}

describe("GET /api/v1/resolve?type=industries (issue #3228)", () => {
  beforeEach(() => {
    mocks.suggestIndustries.mockReset();
  });

  it("emits a slug-shaped string for each industry, NOT the stringified id", async () => {
    mocks.suggestIndustries.mockResolvedValue([
      { id: 3, name: "Technology" },
      { id: 42, name: "Financial Services" },
    ]);

    const { res, body } = await call("?type=industries&q=tech");

    expect(res.status).toBe(200);
    expect(body.type).toBe("industries");
    expect(body.matches).toEqual([
      { slug: "technology", name: "Technology" },
      { slug: "financial-services", name: "Financial Services" },
    ]);
    // Specifically: no entry's slug is a stringified integer.
    for (const m of body.matches!) {
      expect(m.slug).not.toMatch(/^\d+$/);
    }
  });

  it("derives slug from the localized display name (locale=de)", async () => {
    mocks.suggestIndustries.mockResolvedValue([
      // The action localizes `name` per request locale; the route never
      // needs the English name. We assert that whatever name comes back
      // is what gets slugified.
      { id: 3, name: "Technologie" },
    ]);

    const { body } = await call("?type=industries&q=tech&locale=de");

    expect(mocks.suggestIndustries).toHaveBeenCalledWith({
      query: "tech",
      locale: "de",
    });
    expect(body.matches).toEqual([{ slug: "technologie", name: "Technologie" }]);
  });

  it("normalizes punctuation and casing through the canonical slugifier", async () => {
    mocks.suggestIndustries.mockResolvedValue([
      { id: 7, name: "Food & Beverage" },
      { id: 8, name: "Oil + Gas" },
      { id: 9, name: "Pharma / Biotech" },
    ]);

    const { body } = await call("?type=industries&q=foo");

    expect(body.matches).toEqual([
      // `&` -> `and`, `+` -> `plus`, `/` and spaces -> `-`.
      { slug: "food-and-beverage", name: "Food & Beverage" },
      { slug: "oil-plus-gas", name: "Oil + Gas" },
      { slug: "pharma-biotech", name: "Pharma / Biotech" },
    ]);
  });

  it("falls back to stringified id only when the name slugifies to empty", async () => {
    mocks.suggestIndustries.mockResolvedValue([
      // All-symbol name — `slugifyTitle` returns "" for this. The route
      // falls back to `String(id)` so the field is never empty.
      { id: 99, name: "!!!" },
    ]);

    const { body } = await call("?type=industries&q=foo");

    expect(body.matches).toEqual([{ slug: "99", name: "!!!" }]);
  });

  it("caps the response at 10 matches (existing behavior, unchanged)", async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({
      id: i + 1,
      name: `Industry ${i + 1}`,
    }));
    mocks.suggestIndustries.mockResolvedValue(many);

    const { body } = await call("?type=industries&q=ind");

    expect(body.matches).toHaveLength(10);
    // Each capped slug must still be slug-shaped (no bare integers).
    for (const m of body.matches!) {
      expect(m.slug).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
    }
  });
});

describe("GET /api/v1/resolve location service boundary (#3330)", () => {
  beforeEach(() => {
    mocks.suggestLocations.mockReset();
  });

  it("resolves locations through the service tier without the server-action module", async () => {
    mocks.suggestLocations.mockResolvedValue([
      {
        id: 1,
        slug: "zurich",
        name: "Zurich",
        type: "city",
        parentName: "Switzerland",
      },
    ]);

    const { res, body } = await call("?type=locations&q=zur&locale=de");

    expect(res.status).toBe(200);
    expect(mocks.suggestLocations).toHaveBeenCalledWith({
      query: "zur",
      locale: "de",
    });
    expect(body.matches).toEqual([
      {
        slug: "zurich",
        name: "Zurich",
        type: "city",
        parentName: "Switzerland",
      },
    ]);
  });
});
