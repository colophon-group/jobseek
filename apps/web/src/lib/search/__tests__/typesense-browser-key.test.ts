import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const NOW_MS = 2_000_000_000_000;

describe("getTypesenseBrowserConfig", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(NOW_MS);
    vi.stubGlobal("BroadcastChannel", undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("refreshes a signed key before its server-enforced expiry", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({
        apiKey: "first-key",
        host: "typesense.example",
        port: 443,
        protocol: "https",
        expiresAt: NOW_MS + 60_000,
      }))
      .mockResolvedValueOnce(Response.json({
        apiKey: "second-key",
        host: "typesense.example",
        port: 443,
        protocol: "https",
        expiresAt: NOW_MS + 120_000,
      }));
    vi.stubGlobal("fetch", fetchMock);

    const { getTypesenseBrowserConfig } = await import("../typesense-browser-key");

    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "first-key",
    });
    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "first-key",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(31_000);

    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "second-key",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
