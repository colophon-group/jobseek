import { describe, it, expect } from "vitest";
import { scoreColor, formatScore } from "@/lib/queue-utils";

describe("scoreColor", () => {
  it("returns gray for null score", () => {
    expect(scoreColor(null)).toBe("bg-gray-100");
  });

  it("returns green for high score (>=0.8)", () => {
    expect(scoreColor(0.8)).toBe("bg-green-100");
    expect(scoreColor(0.95)).toBe("bg-green-100");
  });

  it("returns blue for medium-high score (0.6-0.8)", () => {
    expect(scoreColor(0.6)).toBe("bg-blue-100");
    expect(scoreColor(0.75)).toBe("bg-blue-100");
  });

  it("returns yellow for medium score (0.4-0.6)", () => {
    expect(scoreColor(0.4)).toBe("bg-yellow-100");
    expect(scoreColor(0.5)).toBe("bg-yellow-100");
  });

  it("returns red for low score (<0.4)", () => {
    expect(scoreColor(0.3)).toBe("bg-red-100");
    expect(scoreColor(0)).toBe("bg-red-100");
  });
});

describe("formatScore", () => {
  it("returns dash for null score", () => {
    expect(formatScore(null)).toBe("—");
  });

  it("formats score as percentage", () => {
    expect(formatScore(0.8)).toBe("80%");
    expect(formatScore(0.5)).toBe("50%");
    expect(formatScore(0.95)).toBe("95%");
  });

  it("rounds score to nearest percent", () => {
    expect(formatScore(0.856)).toBe("86%");
    expect(formatScore(0.844)).toBe("84%");
  });

  it("handles edge cases", () => {
    expect(formatScore(0)).toBe("0%");
    expect(formatScore(1)).toBe("100%");
  });
});
