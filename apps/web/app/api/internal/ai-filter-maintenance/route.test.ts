import { beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  cleanup: vi.fn(),
  sweep: vi.fn(),
}));

vi.mock("@/lib/ai-filter/maintenance-service", () => ({
  cleanupAiFilterRetention: mocks.cleanup,
  sweepAiFilterCatchup: mocks.sweep,
}));

import { GET } from "./route";

withTestEnv({ CRON_SECRET: "test-cron-secret" });

describe("AI filter maintenance route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.cleanup.mockResolvedValue({
      decisionsDeleted: 1,
      cacheEntriesDeleted: 1,
      hasMore: false,
    });
    mocks.sweep.mockResolvedValue({
      selected: 1,
      started: 1,
      failed: 0,
      hasMore: false,
    });
  });

  it("rejects a missing or incorrect cron bearer token", async () => {
    const response = await GET(new Request(
      "https://jseek.co/api/internal/ai-filter-maintenance",
    ));
    expect(response.status).toBe(401);
    expect(mocks.cleanup).not.toHaveBeenCalled();
    expect(mocks.sweep).not.toHaveBeenCalled();
  });

  it("runs bounded retention and catch-up maintenance", async () => {
    const response = await GET(new Request(
      "https://jseek.co/api/internal/ai-filter-maintenance",
      { headers: { authorization: "Bearer test-cron-secret" } },
    ));
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(mocks.cleanup).toHaveBeenCalledOnce();
    expect(mocks.sweep).toHaveBeenCalledOnce();
  });
});
