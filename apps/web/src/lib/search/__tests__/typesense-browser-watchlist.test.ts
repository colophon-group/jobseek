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

import {
  getWatchlistPostingsBrowser,
  getWatchlistPostingYearCountBrowser,
} from "../typesense-browser-watchlist";

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

  it("uses the flow filter for a browser-direct year count", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({ found: 31 }),
    } as Response);
    globalThis.fetch = fetchMock;

    await expect(
      getWatchlistPostingYearCountBrowser({
        companyIds: [makeUuid(1)],
        languages: ["en"],
      }),
    ).resolves.toBe(31);

    const requestUrl = new URL(String(fetchMock.mock.calls[0]?.[0]));
    const filter = requestUrl.searchParams.get("filter_by") ?? "";
    expect(filter).toContain("has_content:!=false");
    expect(filter).toContain("first_seen_at:>");
    expect(filter).toContain(`company_id:[${makeUuid(1)}]`);
    expect(filter).not.toContain("is_active:true");
    expect(requestUrl.searchParams.get("per_page")).toBe("0");
  });

  it("preserves posting locations in a browser refresh", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      json: async () => ({
        found: 1,
        hits: [{
          document: {
            id: "posting-1",
            first_seen_at: 1_700_000_000,
            company_id: "company-1",
            company_name: "Acme",
            company_slug: "acme",
            location_names: ["Zurich", "Switzerland"],
          },
        }],
      }),
    } as Response);
    globalThis.fetch = fetchMock;

    const result = await getWatchlistPostingsBrowser({
      companyIds: [makeUuid(1)],
      offset: 0,
      limit: 20,
    });

    expect(result.postings[0]?.locationNames).toEqual([
      "Zurich",
      "Switzerland",
    ]);
  });
});
