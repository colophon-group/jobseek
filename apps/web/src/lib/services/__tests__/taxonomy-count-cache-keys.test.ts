import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const keys: string[] = [];
  return {
    keys,
    cached: vi.fn((key: string) => {
      keys.push(key);
      return Promise.resolve({});
    }),
  };
});

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
}));
vi.mock("@/db", () => ({ db: { execute: vi.fn() } }));
vi.mock("drizzle-orm", () => ({ sql: vi.fn() }));
vi.mock("@/lib/cache", () => ({ cached: mocks.cached }));
vi.mock("@/lib/cache-ttl", () => ({ CACHE_TTL_LONG: 3600 }));
vi.mock("@/lib/cache-tags", () => ({
  typeaheadOccupationsCacheTag: () => "typeahead:occupations",
  typeaheadSenioritiesCacheTag: () => "typeahead:seniorities",
  typeaheadTechnologiesCacheTag: () => "typeahead:technologies",
}));
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

import { getEmploymentTypeCounts, getWorkModeCounts } from "../taxonomy";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.keys.length = 0;
});

describe("taxonomy count cache keys (#3303)", () => {
  it("canonicalizes workMode filters for employment-type counts", async () => {
    await getEmploymentTypeCounts({ workMode: ["onsite", "remote"] });
    await getEmploymentTypeCounts({ workMode: ["remote", "onsite"] });

    expect(mocks.keys).toHaveLength(2);
    expect(mocks.keys[0]).toBe(mocks.keys[1]);
    expect(mocks.keys[0]).toBe('emp-type-counts:{"workMode":["onsite","remote"]}');
  });

  it("canonicalizes employmentTypes filters for work-mode counts", async () => {
    await getWorkModeCounts({ employmentTypes: ["part_time", "full_time"] });
    await getWorkModeCounts({ employmentTypes: ["full_time", "part_time"] });

    expect(mocks.keys).toHaveLength(2);
    expect(mocks.keys[0]).toBe(mocks.keys[1]);
    expect(mocks.keys[0]).toBe(
      'work-mode-counts:{"employmentTypes":["full_time","part_time"]}',
    );
  });
});
