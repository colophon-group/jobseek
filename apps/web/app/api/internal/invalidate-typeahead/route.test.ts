import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  invalidatePattern: vi.fn(),
  revalidateTag: vi.fn(),
}));

vi.mock("@/lib/cache", () => ({ invalidatePattern: mocks.invalidatePattern }));
vi.mock("next/cache", () => ({ revalidateTag: mocks.revalidateTag }));

import { POST } from "./route";

const _ORIGINAL_TOKEN = process.env.INTERNAL_REVALIDATE_TOKEN;

beforeEach(() => {
  mocks.invalidatePattern.mockReset();
  mocks.invalidatePattern.mockResolvedValue(0);
  mocks.revalidateTag.mockReset();
  process.env.INTERNAL_REVALIDATE_TOKEN = "secret-token";
});

afterEach(() => {
  if (_ORIGINAL_TOKEN === undefined)
    delete process.env.INTERNAL_REVALIDATE_TOKEN;
  else process.env.INTERNAL_REVALIDATE_TOKEN = _ORIGINAL_TOKEN;
});

const _request = (auth?: string) =>
  new Request("http://localhost/api/internal/invalidate-typeahead", {
    method: "POST",
    headers: auth ? { Authorization: auth } : {},
  });

describe("POST /api/internal/invalidate-typeahead", () => {
  it("returns 503 when INTERNAL_REVALIDATE_TOKEN is unset", async () => {
    delete process.env.INTERNAL_REVALIDATE_TOKEN;
    const res = await POST(_request("Bearer secret-token") as never);
    expect(res.status).toBe(503);
    expect(mocks.invalidatePattern).not.toHaveBeenCalled();
    expect(mocks.revalidateTag).not.toHaveBeenCalled();
  });

  it("returns 401 when bearer token is missing", async () => {
    const res = await POST(_request() as never);
    expect(res.status).toBe(401);
    expect(mocks.invalidatePattern).not.toHaveBeenCalled();
    expect(mocks.revalidateTag).not.toHaveBeenCalled();
  });

  it("returns 401 when bearer token is wrong", async () => {
    const res = await POST(_request("Bearer wrong-token") as never);
    expect(res.status).toBe(401);
    expect(mocks.invalidatePattern).not.toHaveBeenCalled();
    expect(mocks.revalidateTag).not.toHaveBeenCalled();
  });

  it("revalidates the migrated `'use cache'` tags on auth", async () => {
    /** PR #2907 follow-up + #2884 bucket 4: the 5 migrated typeaheads
     * write to Next's per-region runtime cache via `'use cache'`, plus
     * (as of #2884 bucket 4) the CSV-driven per-company tag covering
     * `getCompanyBySlug` and `getSimilarCompanies`. Only `revalidateTag`
     * evicts those slots. The legacy Redis prefix sweep stays as a
     * rollout-window backstop. */
    const res = await POST(_request("Bearer secret-token") as never);

    expect(res.status).toBe(200);
    const tags = mocks.revalidateTag.mock.calls.map((c) => c[0]);
    expect(tags).toEqual([
      "typeahead:locations",
      "typeahead:occupations",
      "typeahead:seniorities",
      "typeahead:technologies",
      "typeahead:companies",
      "company-csv-data",
    ]);
    // Each call passes the Next 16 "max" profile so the tag does not
    // expire — see route handler comment.
    for (const call of mocks.revalidateTag.mock.calls) {
      expect(call[1]).toBe("max");
    }
    const body = await res.json();
    expect(body.revalidatedTags).toEqual(tags);
  });

  it("invokes invalidatePattern for every typeahead prefix", async () => {
    mocks.invalidatePattern.mockImplementation(async (prefix: string) =>
      prefix === "loc-suggest:" ? 5 : prefix === "company-suggest:" ? 2 : 0,
    );

    const res = await POST(_request("Bearer secret-token") as never);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.total).toBe(7);
    expect(body.deleted).toEqual({
      "loc-suggest:": 5,
      "occ-suggest:": 0,
      "sen-suggest:": 0,
      "tech-suggest:": 0,
      "company-suggest:": 2,
      "company-slug:": 0,
      "company-similar:": 0,
    });

    const calls = mocks.invalidatePattern.mock.calls.map((c) => c[0]);
    expect(calls).toEqual([
      "loc-suggest:",
      "occ-suggest:",
      "sen-suggest:",
      "tech-suggest:",
      "company-suggest:",
      "company-slug:",
      "company-similar:",
    ]);
  });

  it("sweeps company-detail caches (company-slug + company-similar)", async () => {
    /** Regression for #2715: a company rename via crawler sync would
     * otherwise leave /company/<slug> stale up to the 10-minute TTL on
     * `company-slug:`. The same sweep also covers `company-similar:`,
     * whose ranked-peers result depends on industry membership. */
    mocks.invalidatePattern.mockImplementation(async (prefix: string) =>
      prefix === "company-slug:" ? 4 : prefix === "company-similar:" ? 3 : 0,
    );

    const res = await POST(_request("Bearer secret-token") as never);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.deleted["company-slug:"]).toBe(4);
    expect(body.deleted["company-similar:"]).toBe(3);
    expect(body.total).toBe(7);
  });

  it("does not accept arbitrary prefixes from the caller", async () => {
    /** Defense-in-depth: even an authenticated caller cannot direct the
     * sweep at, say, `cache:session:*` — the prefix list is owned by
     * this route, not the request body. */
    const res = await POST(
      new Request("http://localhost/api/internal/invalidate-typeahead", {
        method: "POST",
        headers: { Authorization: "Bearer secret-token" },
        body: JSON.stringify({ prefixes: ["session:", "auth:"] }),
      }) as never,
    );

    expect(res.status).toBe(200);
    const calls = mocks.invalidatePattern.mock.calls.map((c) => c[0]);
    expect(calls).not.toContain("session:");
    expect(calls).not.toContain("auth:");
  });
});
