import { beforeEach, describe, expect, it, vi } from "vitest";

// `vi.mock` hoists to the top of the file so its factories cannot close
// over module-scope variables. Use `vi.hoisted` to share mocks between
// the factory and the test bodies.
const mocks = vi.hoisted(() => ({
  cached: vi.fn(
    async (
      _key: string,
      fetcher: () => Promise<unknown>,
      _options: { ttl: number; skipIf?: (d: unknown) => boolean },
    ) => fetcher(),
  ),
  search: vi.fn(),
  dbExecute: vi.fn(),
}));

vi.mock("server-only", () => ({}));

// `cached` short-circuits to the fetcher in tests so we exercise the real
// Typesense + Postgres branching, not the Redis cache layer (covered in
// cache.test.ts). The skipIf null-poisoning behavior is asserted via call
// shape, not via Redis writes.
vi.mock("@/lib/cache", () => ({ cached: mocks.cached }));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));

vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));

// Drag in the side-effect-bearing transitive imports as no-ops so the
// "use server" module loads cleanly in vitest.
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
}));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: vi.fn() }));
vi.mock("@/lib/actions/locations", () => ({ expandLocationIds: vi.fn() }));
vi.mock("@/lib/actions/taxonomy", () => ({ expandOccupationIds: vi.fn() }));
vi.mock("@/lib/search", () => ({ getSearchProvider: vi.fn() }));
vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_COMPANIES: 5,
  ANON_MAX_POSTINGS: 10,
}));
vi.mock("@/lib/search/typesense-filters", () => ({ buildFilterString: vi.fn() }));
vi.mock("@/lib/search/pg-filters", () => ({ localesOrNoneClause: vi.fn() }));
vi.mock("@/lib/actions/search-input", () => ({ parseSearchFilters: vi.fn() }));
vi.mock("@/lib/search/params", () => ({
  firstOf: vi.fn(),
  idsOrUndefined: vi.fn(),
  parseRangeParam: vi.fn(),
}));

import { getCompanyBySlug } from "../company";

const searchMock = mocks.search;
const dbExecuteMock = mocks.dbExecute;
const cachedMock = mocks.cached;

const _hit = (overrides: Record<string, unknown> = {}) => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: "https://cdn.x/icon.png",
  logo: null,
  website: "https://acme.example",
  description: "We build things in English.",
  description_de: "Wir bauen Dinge.",
  industry_id: 7,
  industry_name: "Software",
  industry_name_de: "Software",
  employee_count_range: 3,
  founded_year: 2015,
  active_posting_count: 42,
  ...overrides,
});

const _typesenseResponse = (hit: Record<string, unknown> | null) =>
  hit ? { hits: [{ document: hit }] } : { hits: [] };

beforeEach(() => {
  vi.clearAllMocks();
  searchMock.mockReset();
  dbExecuteMock.mockReset();
  cachedMock.mockImplementation(
    async (
      _key: string,
      fetcher: () => Promise<unknown>,
      _options: { ttl: number; skipIf?: (d: unknown) => boolean },
    ) => fetcher(),
  );
});

describe("getCompanyBySlug — Typesense path", () => {
  it("returns the Typesense hit mapped to CompanyDetail", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(_hit()));

    const out = await getCompanyBySlug("acme", "en");

    expect(out).toEqual({
      id: "co-1",
      name: "Acme Corp",
      slug: "acme",
      icon: "https://cdn.x/icon.png",
      logo: null,
      website: "https://acme.example",
      description: "We build things in English.",
      industryId: 7,
      industryName: "Software",
      employeeCountRange: 3,
      foundedYear: 2015,
      activeJobCount: 42,
    });
    expect(searchMock).toHaveBeenCalledWith({
      q: "*",
      filter_by: "slug:=acme",
      per_page: 1,
    });
    expect(dbExecuteMock).not.toHaveBeenCalled();
  });

  it("prefers the locale-specific description when present", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(_hit()));
    const out = await getCompanyBySlug("acme", "de");
    expect(out?.description).toBe("Wir bauen Dinge.");
    expect(out?.industryName).toBe("Software");
  });

  it("falls back to base English description when locale variant is missing", async () => {
    searchMock.mockResolvedValue(
      _typesenseResponse(_hit({ description_fr: undefined })),
    );
    const out = await getCompanyBySlug("acme", "fr");
    expect(out?.description).toBe("We build things in English.");
  });

  it("returns null description when both locale and base are missing", async () => {
    searchMock.mockResolvedValue(
      _typesenseResponse(
        _hit({ description: undefined, description_de: undefined }),
      ),
    );
    const out = await getCompanyBySlug("acme", "de");
    expect(out?.description).toBeNull();
  });

  it("returns null description when locale value is empty string", async () => {
    /** Empty string must be treated as missing so the en fallback fires —
     * otherwise a half-translated company shows the empty string verbatim. */
    searchMock.mockResolvedValue(_typesenseResponse(_hit({ description_de: "" })));
    const out = await getCompanyBySlug("acme", "de");
    expect(out?.description).toBe("We build things in English.");
  });
});

describe("getCompanyBySlug — slug shape guard", () => {
  /** SLUG_SHAPE rejection short-circuits the Typesense call so attacker input
   * never reaches the raw-interpolated `filter_by`. Postgres still runs
   * (drizzle parameterizes; injection-safe) and returns null naturally. */
  it.each([
    "acme corp", // space
    "acme&&filter:=evil", // injection attempt
    "ACME", // uppercase
    "acme--double", // double hyphen
    "-acme", // leading hyphen
    "acme-", // trailing hyphen
    "acme/path", // slash
    "", // empty
  ])(
    "rejects malformed slug %j without calling Typesense (Postgres still queried, returns null)",
    async (slug) => {
      dbExecuteMock.mockResolvedValue([]);
      const out = await getCompanyBySlug(slug, "en");
      expect(out).toBeNull();
      expect(searchMock).not.toHaveBeenCalled();
      expect(dbExecuteMock).toHaveBeenCalled();
    },
  );

  it.each(["acme", "1-800-flowers", "deutsche-bank", "abc123-xyz"])(
    "accepts well-formed slug %j",
    async (slug) => {
      searchMock.mockResolvedValue(_typesenseResponse(_hit({ slug })));
      const out = await getCompanyBySlug(slug, "en");
      expect(out?.slug).toBe(slug);
    },
  );
});

describe("getCompanyBySlug — Postgres fallback", () => {
  const _pgRow = {
    id: "co-1",
    name: "Acme Corp",
    slug: "acme",
    icon: null,
    logo: null,
    website: null,
    description: "From Postgres",
    industry_id: 7,
    industry_name: "Software",
    employee_count_range: 3,
    founded_year: 2015,
  };

  it("falls through to Postgres when Typesense throws", async () => {
    searchMock.mockRejectedValue(new Error("typesense down"));
    dbExecuteMock.mockResolvedValue([_pgRow]);

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    expect(out?.activeJobCount).toBe(0); // PG path doesn't compute this
    expect(dbExecuteMock).toHaveBeenCalled();
  });

  it("falls through to Postgres when Typesense returns 0 hits", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(null));
    dbExecuteMock.mockResolvedValue([_pgRow]);

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    expect(dbExecuteMock).toHaveBeenCalled();
  });

  it("returns null when both Typesense and Postgres miss", async () => {
    searchMock.mockResolvedValue(_typesenseResponse(null));
    dbExecuteMock.mockResolvedValue([]);

    const out = await getCompanyBySlug("acme", "en");
    expect(out).toBeNull();
  });
});

describe("getCompanyBySlug — caching contract", () => {
  it("caches with TTL 600 and skipIf-null", async () => {
    /** Brand-new slugs Typesense hasn't seen yet must not poison the
     * cache as null — the skipIf predicate is the contract that lets
     * Postgres fallback fill the gap on the next request. */
    searchMock.mockResolvedValue(_typesenseResponse(_hit()));

    await getCompanyBySlug("acme", "en");

    expect(cachedMock).toHaveBeenCalledWith(
      "company-slug:acme:en",
      expect.any(Function),
      expect.objectContaining({
        ttl: 600,
        skipIf: expect.any(Function),
      }),
    );
    const skipIf = cachedMock.mock.calls[0][2].skipIf;
    expect(skipIf(null)).toBe(true);
    expect(skipIf({ id: "x" })).toBe(false);
  });
});
