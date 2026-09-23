import { describe, expect, it } from "vitest";

import {
  JEV_MAX_CALL_RESERVATION_NANODOLLARS,
  canRunAiFilter,
  exceedsAiFilterBudget,
  jevCostNanodollars,
  readAiFilterRuntimePolicy,
} from "./policy";

describe("Jev fixed-point policy", () => {
  it("prices the observed compact and maximum-description calls exactly", () => {
    expect(jevCostNanodollars(1_711, 326)).toBe(71_862);
    expect(jevCostNanodollars(8_234, 328)).toBe(345_828);
  });

  it("bounds each paid call's two-attempt reservation", () => {
    expect(JEV_MAX_CALL_RESERVATION_NANODOLLARS).toBe(6_720_000);
  });

  it("keeps both monthly ceilings off unless explicitly configured", () => {
    const policy = readAiFilterRuntimePolicy({});
    expect(policy.userMonthlyBudgetNanodollars).toBeNull();
    expect(policy.projectMonthlyBudgetNanodollars).toBeNull();
    expect(() => readAiFilterRuntimePolicy({
      AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS: "0",
    })).toThrow("AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS");
    expect(() => readAiFilterRuntimePolicy({
      AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS: "0",
    })).toThrow("AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS");
  });

  it("permits Jev for entitled owners when both switches are on", () => {
    const policy = readAiFilterRuntimePolicy({
      AI_FILTER_ENABLED: "true",
      AI_FILTER_JEV_1_13_0_ENABLED: "true",
    });
    expect(canRunAiFilter(policy)).toBe(true);
    expect(canRunAiFilter(readAiFilterRuntimePolicy({
      AI_FILTER_ENABLED: "true",
    }))).toBe(false);
    expect(canRunAiFilter(readAiFilterRuntimePolicy({
      AI_FILTER_JEV_1_13_0_ENABLED: "true",
    }))).toBe(false);
  });

  it("enforces an optional ceiling and rejects fixed-point overflow", () => {
    expect(exceedsAiFilterBudget({
      actualNanodollars: 90,
      reservedNanodollars: 0,
      nextReservationNanodollars: 11,
      limitNanodollars: 100,
    })).toBe(true);
    expect(exceedsAiFilterBudget({
      actualNanodollars: 20,
      reservedNanodollars: 5,
      nextReservationNanodollars: 10,
      limitNanodollars: null,
    })).toBe(false);
    expect(() => exceedsAiFilterBudget({
      actualNanodollars: Number.MAX_SAFE_INTEGER,
      reservedNanodollars: 0,
      nextReservationNanodollars: 1,
      limitNanodollars: null,
    })).toThrow("fixed-point range");
  });

  it("reads switches and configurable concurrency without request quotas", () => {
    expect(readAiFilterRuntimePolicy({
      AI_FILTER_ENABLED: "true",
      AI_FILTER_JEV_1_13_0_ENABLED: "true",
      AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS: "10000000000",
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
