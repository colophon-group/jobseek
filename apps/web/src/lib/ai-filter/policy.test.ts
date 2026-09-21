import { describe, expect, it } from "vitest";

import {
  AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS,
  JEV_MAX_CALL_RESERVATION_NANODOLLARS,
  jevCostNanodollars,
  readAiFilterRuntimePolicy,
} from "./policy";

describe("Jev fixed-point policy", () => {
  it("prices the observed compact and maximum-description calls exactly", () => {
    expect(jevCostNanodollars(1_711, 326)).toBe(71_862);
    expect(jevCostNanodollars(8_234, 328)).toBe(345_828);
  });

  it("lets a user approach $10 within one bounded retry reservation", () => {
    const lastAllowed =
      AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS -
      JEV_MAX_CALL_RESERVATION_NANODOLLARS;
    expect(lastAllowed).toBeGreaterThan(9_990_000_000);
    expect(
      AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS - lastAllowed,
    ).toBe(JEV_MAX_CALL_RESERVATION_NANODOLLARS);
  });

  it("fails closed without an explicit project budget", () => {
    expect(() => readAiFilterRuntimePolicy({})).toThrow(
      "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS",
    );
  });

  it("reads switches and configurable concurrency without request quotas", () => {
    expect(readAiFilterRuntimePolicy({
      AI_FILTER_ENABLED: "true",
      AI_FILTER_JEV_1_13_0_ENABLED: "true",
      AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS: "1000000000000",
      AI_FILTER_MAX_SEGMENTS_PER_USER: "3",
      AI_FILTER_MAX_SEGMENTS_PROJECT: "30",
    })).toEqual({
      enabled: true,
      routeEnabled: true,
      userMonthlyBudgetNanodollars: 10_000_000_000,
      projectMonthlyBudgetNanodollars: 1_000_000_000_000,
      maxSegmentsPerUser: 3,
      maxSegmentsPerProject: 30,
    });
  });
});
