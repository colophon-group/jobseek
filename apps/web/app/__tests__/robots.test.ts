import { beforeEach, describe, it, expect, vi } from "vitest";

const { planSitemapShardsMock } = vi.hoisted(() => ({
  planSitemapShardsMock: vi.fn(),
}));

vi.mock("@/lib/sitemap", () => ({
  planSitemapShards: planSitemapShardsMock,
}));

import robots from "../robots";

describe("robots", () => {
  beforeEach(() => {
    planSitemapShardsMock.mockReset();
    planSitemapShardsMock.mockResolvedValue([{ id: 0 }, { id: 1 }, { id: 2 }]);
  });

  it("returns a valid robots config", async () => {
    const result = await robots();
    expect(result.rules).toBeDefined();
  });

  it("allows all user agents", async () => {
    const result = await robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    expect(wildcard).toBeDefined();
  });

  it("allows root path", async () => {
    const result = await robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    expect(wildcard?.allow).toContain("/");
  });

  it("disallows dashboard and auth pages", async () => {
    const result = await robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    expect(wildcard).toBeDefined();
    const disallow = wildcard!.disallow as string[];
    expect(disallow).toContain("/dashboard");
    expect(disallow).toContain("/sign-in");
    expect(disallow).toContain("/sign-up");
  });

  it("disallows locale-prefixed variants of private pages", async () => {
    const result = await robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    const disallow = wildcard!.disallow as string[];
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(disallow).toContain(`/${locale}/dashboard`);
      expect(disallow).toContain(`/${locale}/sign-in`);
      expect(disallow).toContain(`/${locale}/sign-up`);
      expect(disallow).toContain(`/${locale}/settings`);
    }
  });

  it("disallows private API routes but not public v1", async () => {
    const result = await robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    const disallow = wildcard!.disallow as string[];
    expect(disallow).toContain("/api/auth/");
    expect(disallow).toContain("/api/admin/");
    expect(disallow).toContain("/api/stripe/");
    expect(disallow).not.toContain("/api/");
  });

  it("does not locale-prefix API paths", async () => {
    const result = await robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    const disallow = wildcard!.disallow as string[];
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(disallow).not.toContain(`/${locale}/api/auth/`);
    }
  });

  it("declares the sitemap index plus every shard from the planner", async () => {
    // Belt-and-braces: long-tail bots that don't recurse into a
    // <sitemapindex> still need a direct pointer at every shard.
    planSitemapShardsMock.mockResolvedValueOnce([
      { id: 0 },
      { id: 1 },
      { id: 2 },
    ]);

    const result = await robots();
    expect(result.sitemap).toEqual([
      "https://jseek.co/sitemap.xml",
      "https://jseek.co/sitemap/0.xml",
      "https://jseek.co/sitemap/1.xml",
      "https://jseek.co/sitemap/2.xml",
    ]);
  });

  it("falls back to just the index when the planner rejects", async () => {
    // The planner already swallows fetcher errors, but if a future
    // change makes it throw, robots.txt must still ship at least the
    // index URL — same defense-in-depth pattern as sitemap.xml.
    planSitemapShardsMock.mockRejectedValueOnce(new Error("planner blew up"));

    const result = await robots();
    expect(result.sitemap).toEqual(["https://jseek.co/sitemap.xml"]);
  });
});
