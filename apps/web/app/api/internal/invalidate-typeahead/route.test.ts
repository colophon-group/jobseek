import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  invalidatePattern: vi.fn(),
}));

vi.mock("@/lib/cache", () => ({ invalidatePattern: mocks.invalidatePattern }));

import { POST } from "./route";

const _ORIGINAL_TOKEN = process.env.INTERNAL_REVALIDATE_TOKEN;

beforeEach(() => {
  mocks.invalidatePattern.mockReset();
  mocks.invalidatePattern.mockResolvedValue(0);
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
  });

  it("returns 401 when bearer token is missing", async () => {
    const res = await POST(_request() as never);
    expect(res.status).toBe(401);
    expect(mocks.invalidatePattern).not.toHaveBeenCalled();
  });

  it("returns 401 when bearer token is wrong", async () => {
    const res = await POST(_request("Bearer wrong-token") as never);
    expect(res.status).toBe(401);
    expect(mocks.invalidatePattern).not.toHaveBeenCalled();
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
    });

    const calls = mocks.invalidatePattern.mock.calls.map((c) => c[0]);
    expect(calls).toEqual([
      "loc-suggest:",
      "occ-suggest:",
      "sen-suggest:",
      "tech-suggest:",
      "company-suggest:",
    ]);
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
