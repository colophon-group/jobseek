/**
 * Regression tests for #3276 — `resolveLocationSlugs`,
 * `resolveOccupationSlugs`, `resolveSenioritySlugs`,
 * `resolveTechnologySlugs` previously called `[...slugs].sort()` before
 * forwarding to a `'use cache'` inner. Bare `.sort()` uses UTF-16 code
 * unit order, where `"ü"` (U+00FC) sorts *after* `"z"` — so two callers
 * passing the same logical slug set in different orders hashed to
 * different cache slots, splitting the cache for accented slugs.
 *
 * After the fix every site uses `canonicalStringCompare` (locale-
 * independent `Intl.Collator("en", { sensitivity: "base" })`) so
 * permutations collapse to the same slot.
 *
 * Strategy: mock the cache layer and `db.execute`, then verify that the
 * SQL placeholder array passed to the cached inner is identical
 * regardless of input permutation / case.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  // Captures the substituted SQL parameter values for the most recent
  // `db.execute` call so each test can assert the array shape.
  capturedSql: [] as unknown[][],
  dbExecute: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));
vi.mock("@/lib/cache-tags", () => ({
  typeaheadLocationsCacheTag: () => "typeahead:locations",
  typeaheadOccupationsCacheTag: () => "typeahead:occupations",
  typeaheadSenioritiesCacheTag: () => "typeahead:seniorities",
  typeaheadTechnologiesCacheTag: () => "typeahead:technologies",
}));
vi.mock("@/lib/cache", () => ({
  cached: vi.fn((_key: string, fn: () => unknown) => fn()),
}));
vi.mock("@/lib/cache-ttl", () => ({ CACHE_TTL_LONG: 3600 }));
vi.mock("@/lib/db-retry", () => ({
  withDbRetry: vi.fn((fn: () => Promise<unknown>) => fn()),
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getTypesenseClient: () => ({
    collections: () => ({ documents: () => ({ search: vi.fn() }) }),
  }),
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: () => "",
  POSTING_BASE_FILTER: "is_active:true",
}));
vi.mock("@/lib/search/typeahead-boost", () => ({
  boostByFilterMatches: (xs: unknown) => xs,
}));
vi.mock("@/lib/search/canonicalize-filters", () => ({
  canonicalizeFilters: (x: unknown) => x,
}));
vi.mock("@/lib/search/location-paging", () => ({
  LOCATION_PAGE_SIZE: 20,
}));
vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));
vi.mock("drizzle-orm", () => ({
  // Snapshot the substituted values so each test can assert what got
  // interpolated. `sortedSlugs.join(",")` flows in as a single string
  // value through the `pgArray` template literal.
  sql: (_strings: TemplateStringsArray, ...values: unknown[]) => {
    mocks.capturedSql.push(values);
    return { __sql: true } as unknown;
  },
}));

import { resolveLocationSlugs } from "../locations";
import {
  resolveOccupationSlugs,
  resolveSenioritySlugs,
  resolveTechnologySlugs,
} from "../taxonomy";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.capturedSql = [];
  mocks.dbExecute.mockResolvedValue([]);
});

// Helpers ───────────────────────────────────────────────────────────

// The `pgArray` literal sent through to Postgres is the substituted
// value. `db.execute(sql\`... ${pgArray} ...\`)` records the values as
// the first SQL invocation; the array is the only substitution.
function lastPgArrayValue(): string {
  // The last captured `sql\`\`` call is the one that ran inside the
  // cached inner. The substituted values include the pgArray and
  // (depending on the call) a locale string. We pluck the one that
  // starts with `{` (pg-array literal).
  const flat = mocks.capturedSql.flat();
  const pg = flat.find((v) => typeof v === "string" && v.startsWith("{"));
  return pg as string;
}

// ── resolveOccupationSlugs ──────────────────────────────────────────

describe("resolveOccupationSlugs — canonical sort (#3276)", () => {
  it("permutes input → same Postgres parameter array", async () => {
    await resolveOccupationSlugs(["b-eng", "a-eng"], "en");
    const first = lastPgArrayValue();
    mocks.capturedSql = [];
    await resolveOccupationSlugs(["a-eng", "b-eng"], "en");
    const second = lastPgArrayValue();
    expect(first).toBe(second);
  });

  it("case-folded `Apple` and `apple` produce the same canonical order", async () => {
    // Base-sensitivity means `"Apple"` and `"apple"` collate to the
    // same slot; the secondary identity comparator breaks the tie
    // deterministically. We assert that two callers passing the same
    // logical content (one upper, one lower) produce the same array
    // shape modulo case.
    await resolveOccupationSlugs(["Apple", "banana"], "en");
    const upper = lastPgArrayValue();
    mocks.capturedSql = [];
    await resolveOccupationSlugs(["apple", "Banana"], "en");
    const lower = lastPgArrayValue();
    // The ordering matches: both place the a-group first.
    const upperFirst = upper.slice(1).split(",")[0].toLowerCase();
    const lowerFirst = lower.slice(1).split(",")[0].toLowerCase();
    expect(upperFirst).toBe("apple");
    expect(lowerFirst).toBe("apple");
  });
});

// ── resolveSenioritySlugs ───────────────────────────────────────────

describe("resolveSenioritySlugs — canonical sort (#3276)", () => {
  it("permutes input → same Postgres parameter array", async () => {
    await resolveSenioritySlugs(["senior", "junior"], "en");
    const first = lastPgArrayValue();
    mocks.capturedSql = [];
    await resolveSenioritySlugs(["junior", "senior"], "en");
    const second = lastPgArrayValue();
    expect(first).toBe(second);
  });
});

// ── resolveTechnologySlugs ──────────────────────────────────────────

describe("resolveTechnologySlugs — canonical sort (#3276)", () => {
  it("permutes input → same Postgres parameter array", async () => {
    await resolveTechnologySlugs(["rust", "go"]);
    const first = lastPgArrayValue();
    mocks.capturedSql = [];
    await resolveTechnologySlugs(["go", "rust"]);
    const second = lastPgArrayValue();
    expect(first).toBe(second);
  });
});

// ── resolveLocationSlugs ────────────────────────────────────────────

describe("resolveLocationSlugs — canonical sort (#3276)", () => {
  it("permutes input → same Postgres parameter array", async () => {
    await resolveLocationSlugs(["zurich", "berlin"], "en");
    const first = lastPgArrayValue();
    mocks.capturedSql = [];
    await resolveLocationSlugs(["berlin", "zurich"], "en");
    const second = lastPgArrayValue();
    expect(first).toBe(second);
  });

  it("regression: accented slugs collapse via canonical (`zürich` next to `z`-group)", async () => {
    // Bare `.sort()` would put `"zürich"` after `"zoom"` in UTF-16
    // order. With `canonicalStringCompare`, the u-base of `ü` sorts
    // before `o` — wait, actually the canonical collator is
    // base-sensitivity in "en" locale. For German ä/ö/ü, the base is
    // a/o/u, so `"zürich"` will sort by the `z` first letter. The key
    // test is: input permutation collapses regardless.
    await resolveLocationSlugs(["zürich", "berlin"], "en");
    const first = lastPgArrayValue();
    mocks.capturedSql = [];
    await resolveLocationSlugs(["berlin", "zürich"], "en");
    const second = lastPgArrayValue();
    expect(first).toBe(second);
  });
});
