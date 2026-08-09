import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { timeAgoShort } from "../time";

const NOW = new Date("2026-07-22T12:00:00Z");

describe("timeAgoShort", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("preserves the compact English labels", () => {
    expect(timeAgoShort(new Date(NOW.getTime() - 51 * 60_000))).toBe("51m");
    expect(timeAgoShort(new Date(NOW.getTime() - 3 * 60 * 60_000))).toBe("3h");
    expect(timeAgoShort(new Date(NOW.getTime() - 2 * DAY_MS))).toBe("2d");
    expect(timeAgoShort(new Date(NOW.getTime() - 21 * DAY_MS))).toBe("3w");
    expect(timeAgoShort(new Date(NOW.getTime() - 90 * DAY_MS))).toBe("3mo");
    expect(timeAgoShort(new Date(NOW.getTime() - 730 * DAY_MS))).toBe("2y");
  });

  it("uses locale-aware narrow units", () => {
    const minutesAgo = new Date(NOW.getTime() - 51 * 60_000);
    const monthsAgo = new Date(NOW.getTime() - 90 * DAY_MS);

    expect(timeAgoShort(minutesAgo, "de")).toBe("51 Min.");
    expect(timeAgoShort(monthsAgo, "de")).toBe("3 M");
    expect(timeAgoShort(minutesAgo, "fr")).toBe("51min");
    expect(timeAgoShort(monthsAgo, "fr")).toBe("3m.");
    expect(timeAgoShort(minutesAgo, "it")).toBe("51min");
    expect(timeAgoShort(monthsAgo, "it")).toBe("3 mesi");
  });
});

const DAY_MS = 24 * 60 * 60 * 1000;
