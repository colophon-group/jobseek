import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getTypesenseBrowserConfig: vi.fn(async () => ({
    apiKey: "test-key",
    host: "typesense.test",
    port: 443,
    protocol: "https",
    expiresAt: Date.now() + 60_000,
  })),
}));

vi.mock("../typesense-browser-key", () => ({
  getTypesenseBrowserConfig: mocks.getTypesenseBrowserConfig,
}));

import { getWatchlistPostingsBrowser } from "../typesense-browser-watchlist";

function makeUuid(index: number): string {
  return `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`;
}

describe("getWatchlistPostingsBrowser (#3477)", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it("falls back before sending an oversized company-id filter", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    globalThis.fetch = fetchMock;

    await expect(
      getWatchlistPostingsBrowser({
        companyIds: Array.from({ length: 99 }, (_, i) => makeUuid(i + 1)),
        offset: 0,
        limit: 20,
      }),
    ).rejects.toThrow("watchlist Typesense query exceeds GET limit");

    expect(mocks.getTypesenseBrowserConfig).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
