import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ search: vi.fn() }));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/search/typesense-client", () => ({
  getTypesenseClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));

import {
  fetchLocationDocumentsBySlugs,
  fetchLocationDocumentsWithAncestors,
  fetchOccupationDocuments,
  fetchTechnologyDocuments,
} from "../typesense-taxonomy";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Typesense taxonomy provider", () => {
  it("escapes exact slug literals", async () => {
    mocks.search.mockResolvedValue({ found: 0, hits: [] });

    await fetchLocationDocumentsBySlugs(["cote`d\\azur"]);

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({
        filter_by: "slug:[`cote\\`d\\\\azur`]",
      }),
    );
  });

  it("paginates complete taxonomy snapshots", async () => {
    const firstPage = Array.from({ length: 250 }, (_, index) => ({
      document: {
        id: String(index),
        technology_id: index,
        slug: `technology-${index}`,
        name: `Technology ${index}`,
      },
    }));
    mocks.search
      .mockResolvedValueOnce({ found: 251, hits: firstPage })
      .mockResolvedValueOnce({
        found: 251,
        hits: [
          {
            document: {
              id: "250",
              technology_id: 250,
              slug: "technology-250",
              name: "Technology 250",
            },
          },
        ],
      });

    const documents = await fetchTechnologyDocuments();

    expect(documents).toHaveLength(251);
    expect(mocks.search).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ page: 1, per_page: 250 }),
    );
    expect(mocks.search).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ page: 2, per_page: 250 }),
    );
  });

  it("prefers the requested locale and falls back per occupation", async () => {
    mocks.search.mockResolvedValue({
      found: 3,
      hits: [
        { document: { id: "1-en", occupation_id: 1, slug: "developer", name: "Developer", locale: "en" } },
        { document: { id: "1-de", occupation_id: 1, slug: "developer", name: "Entwickler", locale: "de" } },
        { document: { id: "2-en", occupation_id: 2, slug: "designer", name: "Designer", locale: "en" } },
      ],
    });

    const documents = await fetchOccupationDocuments("de");

    expect(documents.map(({ occupation_id, name }) => [occupation_id, name])).toEqual([
      [1, "Entwickler"],
      [2, "Designer"],
    ]);
    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ filter_by: "locale:[de,en]" }),
    );
  });

  it("walks missing geographic parents without a database read", async () => {
    mocks.search
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: { id: "10", location_id: 10, slug: "zurich", name_en: "Zurich", type: "city", parent_id: 20 } }],
      })
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: { id: "20", location_id: 20, slug: "zurich-canton", name_en: "Zurich", type: "region", parent_id: 30 } }],
      })
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: { id: "30", location_id: 30, slug: "switzerland", name_en: "Switzerland", type: "country" } }],
      });

    const documents = await fetchLocationDocumentsWithAncestors([10]);

    expect(documents.map((document) => document.location_id)).toEqual([10, 20, 30]);
    expect(mocks.search).toHaveBeenCalledTimes(3);
  });

  it("sanitizes credentialed client errors before they leave the provider", async () => {
    const raw = Object.assign(new Error("timeout SECRET_CANARY_MESSAGE"), {
      code: "ECONNABORTED",
      status: 0,
      config: {
        headers: { "X-TYPESENSE-API-KEY": "SECRET_CANARY_HEADER" },
      },
      request: { responseURL: "https://SECRET_CANARY_URL.example" },
    });
    mocks.search.mockRejectedValueOnce(raw);

    let rejection: unknown;
    try {
      await fetchLocationDocumentsBySlugs(["india"]);
    } catch (err) {
      rejection = err;
    }

    expect(rejection).toBeInstanceOf(Error);
    expect(rejection).not.toBe(raw);
    expect(rejection).toMatchObject({
      message: "Typesense request failed",
      code: "ECONNABORTED",
    });
    expect(rejection).not.toHaveProperty("config");
    expect(rejection).not.toHaveProperty("request");
    expect(JSON.stringify(rejection)).not.toContain("SECRET_CANARY");
  });
});
