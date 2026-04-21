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

  it("disallows dashboard and auth pages", () => {
    const result = robots();
    const rules = Array.isArray(result.rules) ? result.rules : [result.rules];
    const wildcard = rules.find((r) => r.userAgent === "*");
    expect(wildcard).toBeDefined();
    const disallow = wildcard!.disallow as string[];
    expect(disallow).toContain("/dashboard");
    expect(disallow).toContain("/sign-in");
    expect(disallow).toContain("/sign-up");
  });

  it("disallows locale-prefixed variants of private pages", () => {
    const result = robots();
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

  it("includes sitemap URL", () => {
    const result = robots();
    expect(result.sitemap).toContain("sitemap.xml");
  });
});
