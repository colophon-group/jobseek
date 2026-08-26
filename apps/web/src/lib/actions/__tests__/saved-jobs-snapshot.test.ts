import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getSessionUserId: vi.fn(),
  fetchIndexedPostingStates: vi.fn(),
  selectQueue: [] as unknown[][],
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/lib/search/typesense-posting-detail", () => ({
  fetchIndexedPostingSnapshot: vi.fn(),
  fetchIndexedPostingStates: mocks.fetchIndexedPostingStates,
}));

vi.mock("@/db/schema", () => {
  const column = (name: string) => ({ name });
  return {
    savedJob: {
      id: column("id"),
      userId: column("user_id"),
      jobPostingId: column("job_posting_id"),
      savedAt: column("saved_at"),
      status: column("status"),
      postingTitle: column("posting_title"),
      postingSourceUrl: column("posting_source_url"),
      postingFirstSeenAt: column("posting_first_seen_at"),
      postingIsActive: column("posting_is_active"),
      companyId: column("company_id"),
      companyName: column("company_name"),
      companySlug: column("company_slug"),
      companyIcon: column("company_icon"),
    },
  };
});

vi.mock("drizzle-orm", () => ({
  eq: vi.fn(),
  and: vi.fn(),
  desc: vi.fn(),
  count: vi.fn(),
}));

vi.mock("@/db", () => {
  const selectChain = () => {
    const chain: Record<string, unknown> = {};
    for (const method of ["from", "where", "orderBy", "offset", "limit"]) {
      chain[method] = () => chain;
    }
    chain.then = (
      resolve: (value: unknown[]) => unknown,
      reject?: (error: unknown) => unknown,
    ) => {
      const rows = mocks.selectQueue.shift();
      if (!rows) {
        return Promise.reject(new Error("select queue empty")).then(
          resolve,
          reject,
        );
      }
      return Promise.resolve(rows).then(resolve, reject);
    };
    return chain;
  };
  return { db: { select: () => selectChain() } };
});

import { getSavedJobs } from "../saved-jobs";

const savedAt = new Date("2026-07-01T12:00:00Z");
const snapshotRow = {
  id: "saved-1",
  savedAt,
  postingId: "posting-1",
  postingTitle: "Archived role",
  postingSourceUrl: "https://example.com/archived",
  postingFirstSeenAt: new Date("2026-01-01T12:00:00Z"),
  postingIsActive: false,
  companyId: "company-1",
  companyName: "Snapshot Company",
  companySlug: "snapshot-company",
  companyIcon: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.selectQueue = [];
  mocks.getSessionUserId.mockResolvedValue("user-1");
  mocks.fetchIndexedPostingStates.mockResolvedValue(new Map());
});

describe("saved-job snapshot reads", () => {
  it("keeps a missing-index inactive posting visible from its snapshot", async () => {
    mocks.selectQueue.push([{ count: 1 }], [snapshotRow]);

    const result = await getSavedJobs({ offset: 0, limit: 20 });

    expect(result.jobs).toHaveLength(1);
    expect(result.jobs[0]).toMatchObject({
      posting: {
        id: "posting-1",
        title: "Archived role",
        isActive: false,
      },
      company: {
        id: "company-1",
        name: "Snapshot Company",
        slug: "snapshot-company",
      },
    });
  });

  it("uses the live active state and outbound URL with snapshot outage fallback", async () => {
    mocks.selectQueue.push([{ count: 1 }], [snapshotRow]);
    mocks.fetchIndexedPostingStates.mockResolvedValueOnce(
      new Map([
        [
          "posting-1",
          {
            isActive: true,
            sourceUrl: "https://example.com/current-locale",
          },
        ],
      ]),
    );

    const live = await getSavedJobs({ offset: 0, limit: 20 });
    expect(live.jobs[0].posting.isActive).toBe(true);
    expect(live.jobs[0].posting.sourceUrl).toBe(
      "https://example.com/current-locale",
    );

    mocks.selectQueue.push([{ count: 1 }], [snapshotRow]);
    mocks.fetchIndexedPostingStates.mockResolvedValueOnce(new Map());
    const degraded = await getSavedJobs({ offset: 0, limit: 20 });
    expect(degraded.jobs[0].posting.isActive).toBe(false);
    expect(degraded.jobs[0].posting.sourceUrl).toBe(
      "https://example.com/archived",
    );
  });

  it("surfaces an incomplete persisted snapshot instead of fabricating links", async () => {
    mocks.selectQueue.push(
      [{ count: 1 }],
      [{ ...snapshotRow, companySlug: null }],
    );

    await expect(getSavedJobs({ offset: 0, limit: 20 })).rejects.toThrow(
      "incomplete company_slug",
    );
  });
});
