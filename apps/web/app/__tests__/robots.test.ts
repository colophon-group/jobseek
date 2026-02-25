import { describe, it, expect } from "vitest";
import robots from "../robots";

describe("robots", () => {
  it("returns a valid robots config", () => {
    const result = robots();
    expect(result.rules).toBeDefined();
  });

  it("allows all user agents", () => {
    const result = robots();
    expect(result.rules).toHaveProperty("userAgent", "*");
  });

  it("allows root path", () => {
    const result = robots();
    expect(result.rules).toHaveProperty("allow", "/");
  });

  it("disallows dashboard and auth pages", () => {
    const result = robots();
    const rules = result.rules;
    expect(rules).toBeDefined();
    const disallow = (rules as { disallow: string[] }).disallow;
    expect(disallow).toContain("/dashboard");
    expect(disallow).toContain("/sign-in");
    expect(disallow).toContain("/sign-up");
  });

  it("includes sitemap URL", () => {
    const result = robots();
    expect(result.sitemap).toContain("sitemap.xml");
  });
});
