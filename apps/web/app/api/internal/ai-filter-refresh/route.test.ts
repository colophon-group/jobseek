import { beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

vi.mock("server-only",()=>({}));
const mocks = vi.hoisted(() => ({ run: vi.fn(), inspect:vi.fn() }));
vi.mock("@/lib/ai-filter/recovery-service",()=>({inspectAiFilterRecovery:mocks.inspect}));
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
  it("retires scheduled dispatch without executing work", async () => {
    const response = await GET(request("Bearer test-cron"));
    expect(response.status).toBe(410);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.json()).toEqual({ error: "execution_moved_to_hetzner" });
    expect(mocks.run).not.toHaveBeenCalled();
  });
  it("protects read-only recovery inspection and never dispatches work",async ()=> {
    mocks.inspect.mockResolvedValue({reviewed:0,legacyWorkersFenced:true});
    const response=await GET(new Request("https://jseek.co/api/internal/ai-filter-refresh?inspect=ff96b7d8-db7e-43a2-aafd-e4007fa68fa9",{headers:{authorization:"Bearer test-cron"}}));
    expect(response.status).toBe(200);expect(mocks.run).not.toHaveBeenCalled();
    expect(await response.json()).toMatchObject({recovery:{reviewed:0}});
  });
});
