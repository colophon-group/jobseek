import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  importLatestMetaApifyRun,
  mapApifyDatasetItems,
  type MetaApifyImportDeps,
} from "../meta-apify-import";

describe("mapApifyDatasetItems", () => {
  it("maps Apify items, skips rows without urls, and deduplicates by url", () => {
    const result = mapApifyDatasetItems([
      {
        title: "Missing URL",
      },
      {
        url: "https://example.com/jobs/1",
        title: "First title",
        teams: ["Engineering"],
        subTeams: ["Infra"],
        responsibilities: "Build systems",
      },
      {
        url: "https://example.com/jobs/1",
        title: "Updated title",
        qualifications: "TypeScript",
      },
    ]);

    expect(result.skippedMissingUrl).toBe(1);
    expect(result.jobs).toHaveLength(1);
    expect(result.jobs[0]).toMatchObject({
      url: "https://example.com/jobs/1",
      title: "Updated title",
      extras: { qualifications: "TypeScript" },
    });
  });
});

describe("importLatestMetaApifyRun", () => {
  it("inserts new jobs, updates existing jobs, and tracks R2 writes", async () => {
    const insertPosting = vi.fn(async ({ sourceUrl }: { sourceUrl: string }) => ({
      id: sourceUrl.endsWith("/new") ? "posting-new" : "posting-other",
    }));
    const updatePosting = vi.fn(async () => undefined);
    const updatePostingHash = vi.fn(async () => undefined);
    const persistDescription = vi
      .fn<MetaApifyImportDeps["persistDescription"]>()
      .mockResolvedValueOnce({ status: "uploaded", hash: BigInt(1) })
      .mockResolvedValueOnce({ status: "unchanged", hash: BigInt(9) });

    const deps: MetaApifyImportDeps = {
      now: () => new Date("2026-03-14T12:00:00.000Z"),
      getBoardConfig: async () => ({
        boardId: "board-1",
        boardSlug: "meta-careers",
        companyId: "company-1",
        actorId: "actor-1",
      }),
      getLatestDataset: async () => ({
        actorId: "actor-1",
        runId: "run-1",
        datasetId: "dataset-1",
        items: [
          {
            url: "https://jobs.example.com/new",
            title: "New role",
            description: "<p>Hello</p>",
          },
          {
            url: "https://jobs.example.com/existing",
            title: "Existing role",
            description: "<p>Updated</p>",
          },
        ],
      }),
      getExistingPostings: async () =>
        new Map([
          [
            "https://jobs.example.com/existing",
            { id: "posting-existing", descriptionR2Hash: BigInt(9) },
          ],
        ]),
      insertPosting,
      updatePosting,
      updatePostingHash,
      resolveLocations: async () => ({ locationIds: null, locationTypes: null }),
      persistDescription,
    };

    const result = await importLatestMetaApifyRun(deps);

    expect(result).toMatchObject({
      fetched: 2,
      inserted: 1,
      updated: 1,
      r2Uploaded: 1,
      r2Unchanged: 1,
      skippedMissingUrl: 0,
    });
    expect(insertPosting).toHaveBeenCalledTimes(1);
    expect(updatePosting).toHaveBeenCalledTimes(1);
    expect(updatePostingHash).toHaveBeenCalledTimes(1);
  });
});
