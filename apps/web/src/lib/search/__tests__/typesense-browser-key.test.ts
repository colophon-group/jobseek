import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const NOW_MS = 2_000_000_000_000;

describe("getTypesenseBrowserConfig", () => {
  let storage: Map<string, string>;

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(NOW_MS);
    storage = new Map();
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => storage.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => storage.set(key, value)),
      removeItem: vi.fn((key: string) => storage.delete(key)),
    });
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
    expect(localStorage.setItem).toHaveBeenCalledOnce();
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

  it("reuses a valid persisted config across module reloads", async () => {
    storage.set("typesense-browser-config-v1", JSON.stringify({
      apiKey: "persisted-key",
      host: "typesense.example",
      port: 443,
      protocol: "https",
      expiresAt: NOW_MS + 120_000,
    }));
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const { getTypesenseBrowserConfig } = await import("../typesense-browser-key");

    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "persisted-key",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("removes an expired persisted config before fetching a replacement", async () => {
    storage.set("typesense-browser-config-v1", JSON.stringify({
      apiKey: "expired-key",
      host: "typesense.example",
      port: 443,
      protocol: "https",
      expiresAt: NOW_MS + 30_000,
    }));
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      apiKey: "fresh-key",
      host: "typesense.example",
      port: 443,
      protocol: "https",
      expiresAt: NOW_MS + 120_000,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getTypesenseBrowserConfig } = await import("../typesense-browser-key");

    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "fresh-key",
    });
    expect(localStorage.removeItem).toHaveBeenCalledWith(
      "typesense-browser-config-v1",
    );
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("drops a revoked persisted child after Typesense returns 401", async () => {
    storage.set("typesense-browser-config-v1", JSON.stringify({
      apiKey: "revoked-key",
      host: "typesense.example",
      port: 443,
      protocol: "https",
      expiresAt: NOW_MS + 120_000,
    }));
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      apiKey: "replacement-key",
      host: "typesense.example",
      port: 443,
      protocol: "https",
      expiresAt: NOW_MS + 120_000,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const {
      getTypesenseBrowserConfig,
      invalidateTypesenseBrowserConfigIfUnauthorized,
    } = await import("../typesense-browser-key");

    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "revoked-key",
    });
    invalidateTypesenseBrowserConfigIfUnauthorized(401);

    expect(localStorage.removeItem).toHaveBeenCalledWith(
      "typesense-browser-config-v1",
    );
    await expect(getTypesenseBrowserConfig()).resolves.toMatchObject({
      apiKey: "replacement-key",
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
