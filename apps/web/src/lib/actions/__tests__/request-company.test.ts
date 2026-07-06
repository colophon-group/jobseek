import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

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
  insertValues: vi.fn(),
  updateSet: vi.fn(),
  updateExec: vi.fn(),
  rateLimit: vi.fn(),
  getClientIp: vi.fn(),
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

vi.mock("@/lib/rate-limit", () => ({
  companyRequestLimiter: { limit: mocks.rateLimit },
  getClientIp: mocks.getClientIp,
}));

// `@/lib/i18n` pulls in `@lingui/react/server`; stubbing keeps the action
// test self-contained. `isLocale` is the only export the action uses.
vi.mock("@/lib/i18n", () => ({
  isLocale: (v: string) => v === "en" || v === "de" || v === "fr" || v === "it",
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
  values: (...args: unknown[]) => {
    mocks.insertValues(...args);
    return {
      returning: () => mocks.insertReturningResult(),
    };
  },
});
const buildUpdateChain = () => ({
  set: (...args: unknown[]) => {
    mocks.updateSet(...args);
    return {
      where: (...args2: unknown[]) => mocks.updateExec(...args2),
    };
  },
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

const GITHUB_APP_TEST_ENV = {
  GITHUB_APP_ID: "12345",
  GITHUB_APP_PRIVATE_KEY: "-----BEGIN KEY-----\nabc\n-----END KEY-----",
  GITHUB_APP_INSTALLATION_ID: "67890",
};

/**
 * Build the headers a real Next-Action POST would carry. Always includes the
 * `next-action` magic header (issue #3235 Layer 3 — server-action header
 * check). Callers can layer on `cf-ipcountry` or `x-vercel-ip-country` via
 * `extra`.
 */
function actionHeaders(extra: Record<string, string> = {}): Headers {
  return new Headers({ "next-action": "abc123", ...extra });
}

describe("requestCompany e2e (#3193) — GitHub issue shape preserved", () => {
  withTestEnv(GITHUB_APP_TEST_ENV);

  beforeEach(() => {
    mocks.issuesCreate.mockReset();
    mocks.createAppAuth.mockReset();
    mocks.OctokitCtor.mockReset();
    mocks.headers.mockReset();
    mocks.selectLimitResult.mockReset();
    mocks.insertReturningResult.mockReset();
    mocks.insertValues.mockReset();
    mocks.updateSet.mockReset();
    mocks.updateExec.mockReset();
    mocks.rateLimit.mockReset();
    mocks.getClientIp.mockReset();

    // Default to "allow" so existing happy-path specs are unchanged.
    mocks.rateLimit.mockResolvedValue({ success: true, remaining: 4 });
    mocks.getClientIp.mockReturnValue("203.0.113.7");
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "CH" }));
  });

  afterEach(() => {
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
    setTestEnv({
      GITHUB_APP_ID: undefined,
      GITHUB_APP_PRIVATE_KEY: undefined,
      GITHUB_APP_INSTALLATION_ID: undefined,
    });

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

/**
 * Issue #3235 — security hardening. The legacy `requestCompany` server action
 * exposed a no-auth, no-rate-limit, no-input-cap path to a real GitHub issue
 * tracker. These specs lock in the three defensive layers added to fix it:
 *
 *   1. **Rate limit** — Wire up the pre-existing-but-dead
 *      `companyRequestLimiter`. Below limit -> through; at limit -> a new
 *      `rate_limited` result kind (mirroring the existing union shape).
 *   2. **Input shape hardening** — `lastUserHint` is locked to a fixed-shape
 *      `{ country?, locale? }` object. Unknown form/header keys are dropped
 *      before the DB insert; invalid `country` / `locale` values are
 *      sanitised (omitted from the row, not rejected — the request still
 *      proceeds because that's the legacy UX contract).
 *   3. **Server-action header check** — Direct (non-action) POSTs that hit
 *      the server-action endpoint without a `Next-Action` header are
 *      rejected before any side effect.
 */
describe("requestCompany security hardening (#3235)", () => {
  withTestEnv(GITHUB_APP_TEST_ENV);

  beforeEach(() => {
    mocks.issuesCreate.mockReset();
    mocks.createAppAuth.mockReset();
    mocks.OctokitCtor.mockReset();
    mocks.headers.mockReset();
    mocks.selectLimitResult.mockReset();
    mocks.insertReturningResult.mockReset();
    mocks.insertValues.mockReset();
    mocks.updateSet.mockReset();
    mocks.updateExec.mockReset();
    mocks.rateLimit.mockReset();
    mocks.getClientIp.mockReset();

    mocks.rateLimit.mockResolvedValue({ success: true, remaining: 4 });
    mocks.getClientIp.mockReturnValue("203.0.113.7");
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "CH" }));
  });

  afterEach(() => {
    vi.resetModules();
  });

  // --- Layer 1: rate limiting -------------------------------------------

  it("Layer 1: returns rate_limited (no DB hit, no GH call) when the limiter blocks", async () => {
    mocks.rateLimit.mockResolvedValue({ success: false, remaining: 0 });

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");

    const result = await requestCompany(null, fd);

    expect(result).toEqual({ success: false, errorCode: "rate_limited" });
    expect(mocks.OctokitCtor).not.toHaveBeenCalled();
    expect(mocks.issuesCreate).not.toHaveBeenCalled();
    expect(mocks.insertValues).not.toHaveBeenCalled();
    expect(mocks.selectLimitResult).not.toHaveBeenCalled();
  });

  it("Layer 1: keys the rate limit on IP AND country so cycling only one axis shares a bucket", async () => {
    mocks.rateLimit.mockResolvedValue({ success: true, remaining: 4 });
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 1 } });
    mocks.getClientIp.mockReturnValue("203.0.113.7");
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "CH" }));

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");
    await requestCompany(null, fd);

    expect(mocks.rateLimit).toHaveBeenCalledTimes(1);
    const key = mocks.rateLimit.mock.calls[0][0] as string;
    expect(key).toContain("203.0.113.7");
    expect(key).toContain("CH");
  });

  it("Layer 1: allows the call through when under the limit", async () => {
    mocks.rateLimit.mockResolvedValue({ success: true, remaining: 4 });
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 7777 } });

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");
    const result = await requestCompany(null, fd);

    expect(result).toEqual({ success: true, issueNumber: 7777 });
    expect(mocks.rateLimit).toHaveBeenCalledTimes(1);
  });

  // --- Layer 2: input shape hardening ----------------------------------

  it("Layer 2: drops unknown FormData keys before the DB insert", async () => {
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 1 } });
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "CH" }));

    const { requestCompany } = await import("../request-company");

    // Attacker stuffs extra keys into the form — none should reach JSONB.
    const fd = new FormData();
    fd.set("input", "Stripe");
    fd.set("locale", "en");
    fd.set("__proto__", "polluted");
    fd.set("admin", "true");
    fd.set("xss", "<script>");
    fd.set("country", "ZZ"); // even a 'country' form field is ignored

    await requestCompany(null, fd);

    expect(mocks.insertValues).toHaveBeenCalledTimes(1);
    const payload = mocks.insertValues.mock.calls[0][0] as {
      input: string;
      lastUserHint: Record<string, unknown> | null;
    };

    expect(payload.lastUserHint).toEqual({ country: "CH", locale: "en" });

    const hintKeys = Object.keys(payload.lastUserHint ?? {});
    expect(hintKeys).toEqual(expect.arrayContaining(["country", "locale"]));
    expect(hintKeys).not.toContain("__proto__");
    expect(hintKeys).not.toContain("admin");
    expect(hintKeys).not.toContain("xss");
    expect(hintKeys).toHaveLength(2);
  });

  it("Layer 2: rejects an out-of-list locale (the row is written without it)", async () => {
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 1 } });
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "CH" }));

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");
    fd.set("locale", "klingon");

    await requestCompany(null, fd);

    const payload = mocks.insertValues.mock.calls[0][0] as {
      lastUserHint: { country?: string; locale?: string } | null;
    };

    expect(payload.lastUserHint).toEqual({ country: "CH" });
    expect(payload.lastUserHint?.locale).toBeUndefined();
  });

  it("Layer 2: rejects a malformed country header (the row is written without it)", async () => {
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 1 } });
    // Garbage country header — 3 chars, mixed case with digits, etc.
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "X1Z" }));

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");
    fd.set("locale", "en");

    await requestCompany(null, fd);

    const payload = mocks.insertValues.mock.calls[0][0] as {
      lastUserHint: { country?: string; locale?: string } | null;
    };

    expect(payload.lastUserHint).toEqual({ locale: "en" });
    expect(payload.lastUserHint?.country).toBeUndefined();
  });

  it("Layer 2: lastUserHint is null when both axes are missing/invalid", async () => {
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 1 } });
    mocks.headers.mockResolvedValue(actionHeaders()); // no country header

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");
    // No locale, no country -> null hint.

    await requestCompany(null, fd);

    const payload = mocks.insertValues.mock.calls[0][0] as {
      lastUserHint: unknown;
    };
    expect(payload.lastUserHint).toBeNull();
  });

  // --- Layer 3: server-action header check ----------------------------

  it("Layer 3: rejects requests without the Next-Action header (no DB, no GH, no rate-limit call)", async () => {
    // No `next-action` header -> direct POST to the action endpoint.
    mocks.headers.mockResolvedValue(new Headers({ "cf-ipcountry": "CH" }));

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");

    const result = await requestCompany(null, fd);

    expect(result).toEqual({ success: false, errorCode: "invalid" });
    expect(mocks.rateLimit).not.toHaveBeenCalled();
    expect(mocks.OctokitCtor).not.toHaveBeenCalled();
    expect(mocks.insertValues).not.toHaveBeenCalled();
  });

  it("Layer 3: accepts requests with the Next-Action header (proceeds to rate-limit + DB)", async () => {
    mocks.selectLimitResult.mockResolvedValue([]);
    mocks.insertReturningResult.mockResolvedValue([{ id: "row-1" }]);
    mocks.issuesCreate.mockResolvedValue({ data: { number: 4242 } });
    mocks.headers.mockResolvedValue(actionHeaders({ "cf-ipcountry": "CH" }));

    const { requestCompany } = await import("../request-company");

    const fd = new FormData();
    fd.set("input", "Stripe");

    const result = await requestCompany(null, fd);

    expect(result).toEqual({ success: true, issueNumber: 4242 });
    expect(mocks.rateLimit).toHaveBeenCalledTimes(1);
  });
});
