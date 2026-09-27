import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ run: vi.fn() }));
vi.mock("@/lib/services/notification-runner", () => ({ runJobAlerts: mocks.run }));
import { GET } from "./route";
beforeEach(() => { mocks.run.mockReset(); vi.stubEnv("CRON_SECRET", "cron-test"); });
afterEach(() => vi.unstubAllEnvs());
describe("job alert cron route", () => {
  it("fails closed on missing configuration or authentication", async () => {
    expect((await GET(new Request("https://jseek.co/api/internal/job-alerts"))).status).toBe(401);
    vi.stubEnv("CRON_SECRET", "");
    expect((await GET(new Request("https://jseek.co/api/internal/job-alerts"))).status).toBe(503);
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("returns only aggregate run results and never caches them", async () => {
    mocks.run.mockResolvedValue({ mode: "shadow", matched: 2, sent: 0 });
    const response = await GET(new Request("https://jseek.co/api/internal/job-alerts", { headers: { authorization: "Bearer cron-test" } }));
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    expect(await response.json()).toEqual({ mode: "shadow", matched: 2, sent: 0 });
  });
});
