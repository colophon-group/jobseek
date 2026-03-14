import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/lib/admin/meta-apify-import", () => ({
  MetaApifyImportError: class MetaApifyImportError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  importLatestMetaApifyRun: vi.fn(),
}));

import { importLatestMetaApifyRun } from "@/lib/admin/meta-apify-import";
import { POST } from "./route";

describe("POST /api/admin/meta/apify-import", () => {
  beforeEach(() => {
    process.env.ADMIN_SECRET = "secret-token";
    vi.mocked(importLatestMetaApifyRun).mockReset();
  });

  it("rejects requests without the expected Basic authorization header", async () => {
    const response = await POST(new Request("http://localhost/api/admin/meta/apify-import", {
      method: "POST",
    }));

    expect(response.status).toBe(401);
    expect(await response.text()).toBe("Unauthorized");
  });

  it("returns the importer payload for authorized requests", async () => {
    vi.mocked(importLatestMetaApifyRun).mockResolvedValue({
      boardSlug: "meta-careers",
      actorId: "actor-1",
      runId: "run-1",
      datasetId: "dataset-1",
      fetched: 10,
      skippedMissingUrl: 1,
      inserted: 4,
      updated: 6,
      r2Uploaded: 3,
      r2Unchanged: 5,
    });

    const response = await POST(new Request("http://localhost/api/admin/meta/apify-import", {
      method: "POST",
      headers: {
        Authorization: "Basic secret-token",
      },
    }));

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      fetched: 10,
      inserted: 4,
      updated: 6,
    });
  });
});
