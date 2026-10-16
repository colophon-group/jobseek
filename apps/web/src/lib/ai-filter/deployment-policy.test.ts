// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyNarrowedDeploymentPolicy } from "./deployment-policy";

const receipt = Buffer.from(JSON.stringify({ schemaVersion: "typesense-stable-candidate-order-readiness-v1",
  keyVersion: "uuid-int64-active-v1", partitions: 256, unresolved: 0, authoritativeCount: 1,
  reconciliationRunId: "ff96b7d8-db7e-43a2-aafd-e4007fa68fa9", benchmarkSha256: "a".repeat(64), completedAt: "2026-10-16T00:00:00Z" })).toString("base64url");
const policy = { version: 1, hmacSha256: "a".repeat(64), typesafeSha256: "b".repeat(64), values: {
  AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true", TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: receipt,
  AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS: null, AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS: "2000000",
  AI_FILTER_MAX_SEGMENTS_PER_USER: null, AI_FILTER_MAX_SEGMENTS_PROJECT: null, STRIPE_ENVIRONMENT: "production", PADDLE_ENVIRONMENT: null,
} };

describe("protected Narrowed runtime policy", () => {
  it("restores unreadable flags, preserves budgets, clears unset values and cannot replace database credentials", () => {
    const env = { ...process.env, AI_FILTER_ENABLED: "", AI_FILTER_JEV_1_13_0_ENABLED: "", AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS: "999", DATABASE_URL: "unchanged" };
    expect(applyNarrowedDeploymentPolicy(JSON.stringify(policy), env)).toEqual(policy);
    expect(env.AI_FILTER_ENABLED).toBe("true");
    expect(env.AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS).toBeUndefined();
    expect(env.DATABASE_URL).toBe("unchanged");
  });
  it("leaves the original strict gate authoritative when there is no snapshot", () => {
    const env = { ...process.env, AI_FILTER_ENABLED: "" };
    expect(applyNarrowedDeploymentPolicy(undefined, env)).toBeNull();
    expect(env.AI_FILTER_ENABLED).toBe("");
  });
  it("rejects injected credentials, malformed receipts and invalid limits without partially changing the environment", () => {
    for (const values of [
      { ...policy.values, DATABASE_URL: "injected" },
      { ...policy.values, AI_FILTER_ENABLED: "" },
      { ...policy.values, TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: "invalid" },
      { ...policy.values, AI_FILTER_MAX_SEGMENTS_PROJECT: "0" },
    ]) {
      const env = { ...process.env, AI_FILTER_ENABLED: "original" };
      expect(() => applyNarrowedDeploymentPolicy(JSON.stringify({ ...policy, values }), env)).toThrow();
      expect(env.AI_FILTER_ENABLED).toBe("original");
    }
  });
});
