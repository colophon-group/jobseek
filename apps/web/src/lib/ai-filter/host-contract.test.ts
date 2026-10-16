// @vitest-environment node
import { describe, expect, it } from "vitest";
import { narrowedHostExecutionState } from "./host-contract";

describe("host deployment execution policy", () => {
  it("requires host readiness when both execution flags are enabled", () => {
    expect(narrowedHostExecutionState(false, { AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true" })).toBe("enabled");
  });
  it.each([["false", "true"], ["true", "false"], ["false", "false"]])("permits an explicit disable (%s, %s)", (enabled, jev) => {
    expect(narrowedHostExecutionState(false, { AI_FILTER_ENABLED: enabled, AI_FILTER_JEV_1_13_0_ENABLED: jev })).toBe("disabled");
  });
  it.each([undefined, "", "TRUE", "opaque"]) ("rejects unreadable or invalid flags (%s), including alongside a disable", value => {
    for (const valid of ["true", "false"]) {
      expect(() => narrowedHostExecutionState(false, { AI_FILTER_ENABLED: value, AI_FILTER_JEV_1_13_0_ENABLED: valid })).toThrow();
      expect(() => narrowedHostExecutionState(false, { AI_FILTER_ENABLED: valid, AI_FILTER_JEV_1_13_0_ENABLED: value })).toThrow();
    }
  });
  it("allows the reviewed source pause independently of unavailable runtime settings", () => {
    expect(narrowedHostExecutionState(true, {})).toBe("disabled");
  });
});
