import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * #3193 — `requestCompany` was previously colocated in `stats.ts` together
 * with `getStats`, which forced `@octokit/rest` + `@octokit/auth-app`
 * (~500–700 KB raw, ~150 KB gzipped) to be eagerly loaded into the server
 * bundle of every route importing anything from `stats.ts`, including
 * `/progress` which only needs the octokit-free `getStats`.
 *
 * These specs lock in the split:
 *   1. Importing `stats.ts` must NOT pull octokit into the module graph
 *      (the whole point of the split).
 *   2. `requestCompany` must still produce the same GitHub issue shape
 *      (title, body, labels, repo) end-to-end so the GH-issue automation
 *      keeps working.
 */

vi.mock("server-only", () => ({}));

// --- shared mocks for the requestCompany e2e specs -----------------------

const mocks = vi.hoisted(() => ({
  issuesCreate: vi.fn(),
  createAppAuth: vi.fn(),
  OctokitCtor: vi.fn(),
  headers: vi.fn(),
  selectLimitResult: vi.fn(),
  insertReturningResult: vi.fn(),
  updateExec: vi.fn(),
}));

vi.mock("@octokit/rest", () => ({
  Octokit: function MockOctokit(opts: unknown) {
    mocks.OctokitCtor(opts);
    return {
      issues: { create: mocks.issuesCreate },
    };
  },
}));

vi.mock("@octokit/auth-app", () => ({
  createAppAuth: mocks.createAppAuth,
}));

vi.mock("next/headers", () => ({
  headers: mocks.headers,
}));

// Drizzle fluent-API stand-ins. requestCompany uses three shapes:
//   db.select({...}).from(...).where(...).limit(1)
//   db.insert(...).values(...).returning({ id })
//   db.update(...).set({...}).where(...)  -- a terminal thenable
const buildSelectChain = () => ({
  from: () => ({
    where: () => ({
      limit: () => mocks.selectLimitResult(),
    }),
  }),
});
const buildInsertChain = () => ({
  values: () => ({
    returning: () => mocks.insertReturningResult(),
  }),
});
const buildUpdateChain = () => ({
  set: () => ({
    where: (...args: unknown[]) => mocks.updateExec(...args),
  }),
});

vi.mock("@/db", () => ({
  db: {
    select: () => buildSelectChain(),
    insert: () => buildInsertChain(),
    update: () => buildUpdateChain(),
  },
}));

// Drizzle helpers — both are referenced only as opaque builder calls in
// requestCompany, so identity stubs are enough for assertion purposes.
vi.mock("drizzle-orm", () => ({
  sql: Object.assign((..._args: unknown[]) => ({ _isSql: true }), {
    raw: (..._args: unknown[]) => ({ _isRaw: true }),
  }),
  eq: (..._args: unknown[]) => ({ _isEq: true }),
}));

vi.mock("@/db/schema", () => ({
  companyRequest: {
    id: { name: "id" },
    input: { name: "input" },
    count: { name: "count" },
    githubIssueNumber: { name: "github_issue_number" },
  },
}));

describe("requestCompany e2e (#3193) — GitHub issue shape preserved", () => {
  const env = process.env;

  beforeEach(() => {
    mocks.issuesCreate.mockReset();
    mocks.createAppAuth.mockReset();
    mocks.OctokitCtor.mockReset();
    mocks.headers.mockReset();
    mocks.selectLimitResult.mockReset();
    mocks.insertReturningResult.mockReset();
    mocks.updateExec.mockReset();

    process.env = {
      ...env,
      GITHUB_APP_ID: "12345",
      GITHUB_APP_PRIVATE_KEY: "-----BEGIN KEY-----\nabc\n-----END KEY-----",
      GITHUB_APP_INSTALLATION_ID: "67890",
    };

    mocks.headers.mockResolvedValue(new Headers({ "cf-ipcountry": "CH" }));
  });

  afterEach(() => {
    process.env = env;
    vi.resetModules();
  });

  it("creates a fresh DB row and a GitHub issue with the expected payload", async () => {
    mocks.selectLimitResult.mockResolvedValue([]); // not found
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.updateExec.mockResolvedValue(undefined);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 4242 } });

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");
    fd.set("locale", "en");

    const result = await requestCompany(null, fd);

    expect(result).toEqual({ success: true, issueNumber: 4242 });

    expect(mocks.OctokitCtor).toHaveBeenCalledTimes(1);
    const octoOpts = mocks.OctokitCtor.mock.calls[0][0] as {
      auth: Record<string, string>;
    };
    expect(octoOpts.auth).toEqual({
      appId: "12345",
      privateKey: "-----BEGIN KEY-----\nabc\n-----END KEY-----",
      installationId: "67890",
    });

    expect(mocks.issuesCreate).toHaveBeenCalledTimes(1);
    const call = mocks.issuesCreate.mock.calls[0][0] as {
      owner: string;
      repo: string;
      title: string;
      body: string;
      labels: string[];
    };
    expect(call.owner).toBe("colophon-group");
    expect(call.repo).toBe("jobseek");
    expect(call.title).toBe("Add company: stripe");
    expect(call.labels).toEqual(["company-request"]);
    expect(call.body).toContain("### User request");
    expect(call.body).toContain("stripe");
    expect(call.body).toContain("**Country:** CH");
    expect(call.body).toContain("**Language:** en");
  });

  it("returns issueCreationFailed=true when GH credentials are missing", async () => {
    process.env = { ...env };
    delete process.env.GITHUB_APP_ID;
    delete process.env.GITHUB_APP_PRIVATE_KEY;
    delete process.env.GITHUB_APP_INSTALLATION_ID;

    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Acme Co");

    const result = await requestCompany(null, fd);

    expect(result).toEqual({ success: true, issueCreationFailed: true });
    expect(mocks.OctokitCtor).not.toHaveBeenCalled();
    expect(mocks.issuesCreate).not.toHaveBeenCalled();
  });

  it("validates input shape before touching octokit or the DB", async () => {
    const { requestCompany } = await import("../request-company");

    const empty = await requestCompany(null, new FormData());
    expect(empty).toEqual({ success: false, errorCode: "empty" });

    const tooShort = new FormData();
    tooShort.set("input", "a");
    expect(await requestCompany(null, tooShort)).toEqual({
      success: false,
      errorCode: "too_short",
    });

    const punct = new FormData();
    punct.set("input", "!!!");
    expect(await requestCompany(null, punct)).toEqual({
      success: false,
      errorCode: "invalid",
    });

    expect(mocks.OctokitCtor).not.toHaveBeenCalled();
    expect(mocks.issuesCreate).not.toHaveBeenCalled();
  });
});
