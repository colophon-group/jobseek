import { describe, it, expect } from "vitest";
import robots from "../robots";

describe("robots", () => {
  it("returns a valid robots config", () => {
    const result = robots();
    expect(result.rules).toBeDefined();
  });

  it("allows all user agents", () => {
    const result = robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    expect(wildcard).toBeDefined();
  });

  it("allows root path", () => {
    const result = robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    expect(wildcard?.allow).toContain("/");
  });

  it("allows crawlers to read noindex on product and account HTML", () => {
    const result = robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    for (const rule of rules) {
      const disallow = rule.disallow as string[];
      for (const prefix of ["", "/en", "/de", "/fr", "/it"]) {
        for (const page of ["explore", "sign-in", "sign-up", "settings", "watchlists", "my-jobs", "progress", "reset-password", "verify-email", "forgot-password", "check-email", "checkout"]) {
          expect(disallow).not.toContain(`${prefix}/${page}`);
        }
      }
    }
  });

  it("disallows private API routes but not public v1", () => {
    const result = robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    const disallow = wildcard!.disallow as string[];
    expect(disallow).toContain("/api/auth/");
    expect(disallow).toContain("/api/admin/");
    expect(disallow).toContain("/api/stripe/");
    expect(disallow).not.toContain("/api/");
  });

  it("does not locale-prefix API paths", () => {
    const result = robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    const disallow = wildcard!.disallow as string[];
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(disallow).not.toContain(`/${locale}/api/auth/`);
    }
  });

  it("declares only the monolithic /sitemap.xml URL while the experiment runs", () => {
    // TEMPORARY: while /sitemap.xml is a single <urlset>, listing
    // shards too would have crawlers fetch overlapping content.
    const result = robots();
    expect(result.sitemap).toBe("https://jseek.co/sitemap.xml");
  });
});
