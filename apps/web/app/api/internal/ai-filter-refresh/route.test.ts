import { beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({ run: vi.fn() }));
vi.mock("@/lib/ai-filter/refresh-service", () => ({ runAiFilterRefreshSweep: mocks.run }));
import { GET } from "./route";
withTestEnv({ AI_FILTER_REFRESH_SECRET: "test-cron" });
beforeEach(() => {
  vi.clearAllMocks();
  mocks.run.mockResolvedValue({ status: "completed", claimed: 1, started: 1, failed: 0, deferred: 0 });
});
const request = (authorization?: string) => new Request("https://jseek.co/api/internal/ai-filter-refresh", {
  headers: authorization ? { authorization } : {},
});

describe("background narrowing cron boundary", () => {
  it.each([undefined, "Bearer wrong", "Bearer test-cron-extra"])("rejects unauthorized invocations", async token => {
    expect((await GET(request(token))).status).toBe(401);
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("fails closed without a cron secret", async () => {
    vi.stubEnv("AI_FILTER_REFRESH_SECRET", "");
    expect((await GET(request())).status).toBe(503);
    expect(mocks.run).not.toHaveBeenCalled();
    vi.unstubAllEnvs();
  });
  it("returns uncached aggregate dispatch evidence", async () => {
    const response = await GET(request("Bearer test-cron"));
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.json()).toMatchObject({ started: 1 });
  });
  it("reports retryable dispatch failures", async () => {
    mocks.run.mockRejectedValue(new Error("private upstream body"));
    const response = await GET(request("Bearer test-cron"));
    expect(response.status).toBe(503);
    expect(JSON.stringify(await response.json())).not.toContain("private upstream body");
  });
});
