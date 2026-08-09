import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  collections: vi.fn(),
  search: vi.fn(),
}));

/**
 * #3193 — Regression guard.
 *
 * `getSiteStats` (the cheap platform-counts query used on `/progress` and
 * elsewhere) must NOT pull `@octokit/rest` / `@octokit/auth-app` into its
 * module graph. The two octokit packages add ~500–700 KB raw / ~150 KB
 * gzipped to any server bundle that ends up evaluating them, and adds
 * ~50–150 ms of cold-start time per region.
 *
 * Strategy: register `vi.mock` factories for both octokit packages that
 * throw on first access, then import `stats.ts`. If anything in the
 * transitive graph of `getSiteStats` touches `@octokit/rest` or
 * `@octokit/auth-app`, the import will throw and the spec will fail.
 */

vi.mock("server-only", () => ({}));

// Octokit packages must remain *unreferenced* by the stats module graph.
// We make them explode loudly if anything in stats.ts's import graph
// touches them.
vi.mock("@octokit/rest", () => {
  throw new Error(
    "@octokit/rest must not be imported by stats.ts (#3193). " +
      "Move requestCompany-style usage into actions/request-company.ts.",
  );
});

vi.mock("@octokit/auth-app", () => {
  throw new Error(
    "@octokit/auth-app must not be imported by stats.ts (#3193). " +
      "Move requestCompany-style usage into actions/request-company.ts.",
  );
});

vi.mock("next/cache", () => ({
  cacheLife: vi.fn(),
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({ collections: mocks.collections }),
}));

vi.mock("@/lib/search/typesense-retry", () => ({
  withTypesenseRetry: (fn: () => Promise<unknown>) => fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.collections.mockImplementation(() => ({
    documents: () => ({ search: mocks.search }),
  }));
});

afterEach(() => {
  vi.resetModules();
});

describe("stats.ts no longer eagerly pulls octokit (#3193)", () => {
  it("can be imported without evaluating @octokit/rest or @octokit/auth-app", async () => {
    // If `stats.ts` (or anything in its transitive graph) imports octokit,
    // the `vi.mock` factory above throws and this import will reject.
    const mod = await import("../stats");
    expect(typeof mod.getSiteStats).toBe("function");
  });

  it("reads company and active-posting counts from Typesense", async () => {
    mocks.search
      .mockResolvedValueOnce({ found: 1_234 })
      .mockResolvedValueOnce({ found: 56_789 });
    const { getSiteStats } = await import("../stats");

    await expect(getSiteStats()).resolves.toEqual({
      companyCount: 1_234,
      jobPostingCount: 56_789,
    });
    expect(mocks.collections).toHaveBeenNthCalledWith(1, "company");
    expect(mocks.collections).toHaveBeenNthCalledWith(2, "job_posting");
    expect(mocks.search).toHaveBeenNthCalledWith(1, {
      q: "*",
      per_page: 0,
    });
    expect(mocks.search).toHaveBeenNthCalledWith(2, {
      q: "*",
      filter_by: "is_active:true",
      per_page: 0,
    });
  });
});
