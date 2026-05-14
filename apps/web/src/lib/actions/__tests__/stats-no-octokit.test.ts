import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * #3193 — Regression guard.
 *
 * `getStats` (the cheap platform-counts query used on `/progress` and
 * elsewhere) must NOT pull `@octokit/rest` / `@octokit/auth-app` into its
 * module graph. The two octokit packages add ~500–700 KB raw / ~150 KB
 * gzipped to any server bundle that ends up evaluating them, and adds
 * ~50–150 ms of cold-start time per region.
 *
 * Strategy: register `vi.mock` factories for both octokit packages that
 * throw on first access, then import `stats.ts`. If anything in the
 * transitive graph of `getStats` touches `@octokit/rest` or
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

vi.mock("@/db", () => ({
  db: {
    select: () => ({
      from: () => Promise.resolve([{ count: 0 }]),
    }),
  },
}));

vi.mock("@/db/schema", () => ({
  company: {},
  jobPosting: { isActive: { name: "is_active" } },
}));

vi.mock("drizzle-orm", () => ({
  sql: Object.assign((..._args: unknown[]) => ({ _isSql: true }), {
    raw: (..._args: unknown[]) => ({ _isRaw: true }),
  }),
}));

afterEach(() => {
  vi.resetModules();
});

describe("stats.ts no longer eagerly pulls octokit (#3193)", () => {
  it("can be imported without evaluating @octokit/rest or @octokit/auth-app", async () => {
    // If `stats.ts` (or anything in its transitive graph) imports octokit,
    // the `vi.mock` factory above throws and this import will reject.
    const mod = await import("../stats");
    expect(typeof mod.getStats).toBe("function");
  });
});
