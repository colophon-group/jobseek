import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getViewerTz,
  isValidViewerTimeZone,
  normalizeViewerTimeZone,
  persistViewerTimeZoneCookie,
  VIEWER_TIME_ZONE_COOKIE,
} from "../viewer-tz";

describe("viewer timezone contract (#7201)", () => {
  beforeEach(() => {
    document.cookie = `${VIEWER_TIME_ZONE_COOKIE}=; Path=/; Max-Age=0`;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("accepts real IANA zones and rejects plausible unknown zones", () => {
    expect(isValidViewerTimeZone("America/New_York")).toBe(true);
    expect(isValidViewerTimeZone("Etc/GMT+5")).toBe(true);
    expect(isValidViewerTimeZone("Mars/Olympus")).toBe(false);
    expect(isValidViewerTimeZone("UTC; SELECT 1")).toBe(false);
    expect(normalizeViewerTimeZone("Mars/Olympus")).toBe("UTC");
  });

  it("persists the browser-resolved IANA zone without a network action", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "America/Los_Angeles",
    });

    expect(getViewerTz()).toBe("America/Los_Angeles");
    expect(persistViewerTimeZoneCookie()).toBe("America/Los_Angeles");
    expect(document.cookie).toContain(
      `${VIEWER_TIME_ZONE_COOKIE}=America%2FLos_Angeles`,
    );
  });

  it("falls back safely when the browser cannot resolve a timezone", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(() => {
      throw new RangeError("Intl unavailable");
    });

    expect(getViewerTz()).toBe("UTC");
    expect(persistViewerTimeZoneCookie()).toBe("UTC");
    expect(document.cookie).toContain(`${VIEWER_TIME_ZONE_COOKIE}=UTC`);
  });
});
