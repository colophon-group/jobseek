import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  cached: vi.fn(),
  dbExecute: vi.fn(),
  typesenseSearch: vi.fn(),
}));

vi.mock("@/lib/cache", () => ({
  cached: mocks.cached,
}));

vi.mock("@/db", () => ({
  db: { execute: mocks.dbExecute },
}));

vi.mock("@/lib/db-retry", () => ({
  withDbRetry: (operation: () => Promise<unknown>) => operation(),
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({
      documents: () => ({ search: mocks.typesenseSearch }),
    }),
  }),
}));

import {
  hasPublicCompanyRoute,
  hasPublicWatchlistRoute,
  hasWatchlistRouteForViewer,
  publicWatchlistRouteStatusCacheKey,
} from "../public-resource-status";

beforeEach(() => {
  mocks.cached.mockReset().mockImplementation(
    async (_key: string, fetcher: () => Promise<unknown>) => fetcher(),
  );
  mocks.dbExecute.mockReset();
  mocks.typesenseSearch.mockReset();
  vi.stubEnv("DATABASE_URL", "postgres://status-test.invalid/db");
  vi.stubEnv("TYPESENSE_HOST", "typesense.example.test");
  vi.stubEnv("TYPESENSE_PORT", "443");
  vi.stubEnv("TYPESENSE_PROTOCOL", "https");
  vi.stubEnv("TYPESENSE_SEARCH_KEY", "test-key");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("hasPublicCompanyRoute", () => {
  it("returns true only after a successful exact Typesense hit", async () => {
    mocks.typesenseSearch.mockResolvedValue({ found: 1, hits: [{}] });

    await expect(hasPublicCompanyRoute("acme")).resolves.toBe(true);
    expect(mocks.typesenseSearch).toHaveBeenCalledWith({
      q: "*",
      filter_by: "slug:=acme",
      include_fields: "id",
      per_page: 1,
    });
    expect(mocks.cached).toHaveBeenCalledWith(
      "public-resource-status:company:acme",
      expect.any(Function),
      { ttl: 60 },
    );
  });

  it("returns false for a definitive miss or invalid slug", async () => {
    mocks.typesenseSearch.mockResolvedValue({ found: 0, hits: [] });

    await expect(hasPublicCompanyRoute("missing-company")).resolves.toBe(false);
    await expect(hasPublicCompanyRoute("unsafe:value")).resolves.toBe(false);
    expect(mocks.typesenseSearch).toHaveBeenCalledTimes(1);
  });

  it("treats a secretless build environment as having no dynamic companies", async () => {
    vi.stubEnv("TYPESENSE_HOST", "");

    await expect(hasPublicCompanyRoute("acme")).resolves.toBe(false);
    expect(mocks.typesenseSearch).not.toHaveBeenCalled();
  });

  it("propagates Typesense failures so callers can fail open", async () => {
    mocks.typesenseSearch.mockRejectedValue(new Error("Typesense unavailable"));

    await expect(hasPublicCompanyRoute("acme")).rejects.toThrow(
      "Typesense unavailable",
    );
  });
});

describe("hasPublicWatchlistRoute", () => {
  it("returns the authoritative public-only EXISTS result", async () => {
    mocks.dbExecute.mockResolvedValue([{ route_exists: true }]);

    await expect(
      hasPublicWatchlistRoute("alice", "backend-jobs"),
    ).resolves.toBe(true);
    expect(mocks.cached).toHaveBeenCalledWith(
      publicWatchlistRouteStatusCacheKey("alice", "backend-jobs"),
      expect.any(Function),
      { ttl: 60 },
    );
  });

  it("makes private and absent rows the same false result", async () => {
    mocks.dbExecute.mockResolvedValue([{ route_exists: false }]);

    await expect(
      hasPublicWatchlistRoute("alice", "private-or-missing"),
    ).resolves.toBe(false);
  });

  it("treats a secretless build environment as having no watchlists", async () => {
    vi.stubEnv("DATABASE_URL", "");

    await expect(
      hasPublicWatchlistRoute("alice", "backend-jobs"),
    ).resolves.toBe(false);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("propagates database failures so callers can fail open", async () => {
    mocks.dbExecute.mockRejectedValue(new Error("Postgres unavailable"));

    await expect(
      hasPublicWatchlistRoute("alice", "backend-jobs"),
    ).rejects.toThrow("Postgres unavailable");
  });
});

describe("hasWatchlistRouteForViewer", () => {
  it("returns the uncached owner-only authorization result", async () => {
    mocks.dbExecute.mockResolvedValue([{ route_exists: true }]);

    await expect(
      hasWatchlistRouteForViewer("alice", "private-list", "owner-1"),
    ).resolves.toBe(true);
    expect(mocks.cached).not.toHaveBeenCalled();
    const query = mocks.dbExecute.mock.calls[0]?.[0];
    expect(String(query)).not.toContain("is_public");
  });

  it("returns false when the verified viewer cannot access the route", async () => {
    mocks.dbExecute.mockResolvedValue([{ route_exists: false }]);

    await expect(
      hasWatchlistRouteForViewer("alice", "private-list", "other-user"),
    ).resolves.toBe(false);
  });

  it("propagates database failures so callers can fail open", async () => {
    mocks.dbExecute.mockRejectedValue(new Error("Postgres unavailable"));

    await expect(
      hasWatchlistRouteForViewer("alice", "private-list", "owner-1"),
    ).rejects.toThrow("Postgres unavailable");
  });
});
