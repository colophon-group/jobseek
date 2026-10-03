import { describe, expect, it } from "vitest";
import { trustedAuthOrigins } from "../auth-trusted-origins";

const production = { VERCEL: "1", VERCEL_ENV: "production", VERCEL_URL: "jobseek-deployment-123.vercel.app" };
describe("trusted staged authentication origins", () => {
  it("trusts the exact platform production deployment alongside configured origins", () => {
    expect(trustedAuthOrigins({ ...production, TRUSTED_ORIGINS: "https://jseek.co,http://localhost:3000" }))
      .toEqual(["https://jseek.co", "http://localhost:3000", "https://jobseek-deployment-123.vercel.app"]);
  });
  it("does not duplicate an explicitly configured exact deployment origin", () => {
    expect(trustedAuthOrigins({ ...production, TRUSTED_ORIGINS: "https://jobseek-deployment-123.vercel.app" }))
      .toEqual(["https://jobseek-deployment-123.vercel.app"]);
  });
  it.each([undefined, "preview", "development", "Production", ""])("does not derive trust in VERCEL_ENV=%s", VERCEL_ENV => {
    expect(trustedAuthOrigins({ ...production, VERCEL_ENV })).toEqual([]);
  });
  it.each([undefined, "0", "true", ""])("requires a real Vercel runtime marker %s", VERCEL => {
    expect(trustedAuthOrigins({ ...production, VERCEL })).toEqual([]);
  });
  it.each([undefined, "https://jobseek.vercel.app", "user@jobseek.vercel.app", "jobseek.vercel.app:443", "jobseek.vercel.app/", "jobseek.vercel.app?x=1", "jobseek.vercel.app#x", "jobseek.vercel.app.evil.test", "jobseek.evil.test", "a.b.vercel.app", "*.vercel.app", " jobseek.vercel.app", "jobseek.vercel.app\n", "-jobseek.vercel.app", "jobseek-.vercel.app", "127.0.0.1", `${"a".repeat(64)}.vercel.app`])("rejects noncanonical platform host %s", VERCEL_URL => {
    expect(trustedAuthOrigins({ ...production, VERCEL_URL, TRUSTED_ORIGINS: "https://jseek.co" })).toEqual(["https://jseek.co"]);
  });
  it("returns only explicit origins outside deployment environments", () => {
    expect(trustedAuthOrigins({ TRUSTED_ORIGINS: "https://jseek.co,,http://localhost:3000" })).toEqual(["https://jseek.co", "http://localhost:3000"]);
  });
});
