import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../typesense-browser-key", () => ({
  getTypesenseBrowserConfig: vi.fn().mockResolvedValue({
    apiKey: "browser-key",
    host: "typesense.example",
    port: 443,
    protocol: "https",
    expiresAt: Date.now() + 60_000,
  }),
  invalidateTypesenseBrowserConfigIfUnauthorized: vi.fn(),
}));

import { TypesenseBrowserProvider } from "../typesense-browser";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TypesenseBrowserProvider.loadSimilarCompanies", () => {
  it.each([[0, true], [10, false]] as const)("loads global peers at offset %i (hasMore=%s)", async (offset, hasMore) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      found: 11,
      hits: [
        {
          document: {
            id: "peer-1",
            slug: "peer-one",
            name: "Peer One",
            icon: "peer.svg",
            active_posting_count: 12,
          },
        },
      ],
    }));
    vi.stubGlobal("fetch", fetchMock);

    const provider = new TypesenseBrowserProvider();
    await expect(
      provider.loadSimilarCompanies("company-1", 7, 1, offset),
    ).resolves.toEqual({
      companies: [
        {
          id: "peer-1",
          slug: "peer-one",
          name: "Peer One",
          icon: "peer.svg",
          activeJobCount: 12,
        },
      ],
      hasMore,
    });

    const requestUrl = new URL(String(fetchMock.mock.calls[0]?.[0]));
    expect(requestUrl.pathname).toBe("/collections/company/documents/search");
    expect(requestUrl.searchParams.get("filter_by")).toBe(
      "industry_id:=7 && active_posting_count:>0 && id:!=company-1",
    );
    expect(requestUrl.searchParams.get("sort_by")).toBe(
      "active_posting_count:desc",
    );
    expect(requestUrl.searchParams.get("limit")).toBe("1");
    expect(requestUrl.searchParams.get("offset")).toBe(String(offset));
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      headers: { "x-typesense-api-key": "browser-key" },
    });
  });
});
