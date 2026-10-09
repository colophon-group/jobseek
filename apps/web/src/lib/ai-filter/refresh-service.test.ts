import { beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));
vi.mock("./workflow-trigger", () => ({ startAiFilterCatchup: vi.fn() }));
import { runAiFilterRefreshSweep } from "./refresh-service";
import { AiFilterNotFoundError } from "./configuration-service";

withTestEnv({
  VERCEL_ENV: "production", AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true",
  TYPESAFE_AI_TOKEN: "test-token", AI_FILTER_CACHE_HMAC_SECRET: "x".repeat(32),
});
const target = {
  configurationId: "config", ownerId: "owner", watchlistId: "watchlist",
  queryVersionId: "query", query: "AI development", candidateLanguages: ["en"],
};
const deps = { claim: vi.fn(), assertScope: vi.fn(), configure: vi.fn(), start: vi.fn() };
beforeEach(() => {
  vi.clearAllMocks();
  deps.claim.mockReset().mockResolvedValue([]).mockResolvedValueOnce([target]);
  deps.assertScope.mockResolvedValue(100);
  deps.configure.mockResolvedValue({ queryVersionId: "current-query" });
  deps.start.mockResolvedValue({ runId: "run" });
});

describe("scheduled narrowed freshness", () => {
  it("starts durable newest-first refresh without any owner page request", async () => {
    const result = await runAiFilterRefreshSweep({ dependencies: deps });
    expect(result).toEqual({ status: "completed", claimed: 1, started: 1, deferred: 0, failed: 0 });
    expect(deps.configure).toHaveBeenCalledWith(expect.objectContaining({
      expectedEnabledQueryVersionId: "query", query: target.query, candidateLanguages: ["en"],
    }));
    expect(deps.start).toHaveBeenCalledWith({
      ownerId: "owner", watchlistId: "watchlist", queryVersionId: "current-query",
      kind: "freshness", demandTargetOffset: 10_000,
    });
    expect(JSON.stringify(result)).not.toContain(target.query);
  });

  it.each([
    ["VERCEL_ENV", "preview"], ["AI_FILTER_ENABLED", "false"],
    ["AI_FILTER_JEV_1_13_0_ENABLED", "false"], ["TYPESAFE_AI_TOKEN", ""],
    ["AI_FILTER_CACHE_HMAC_SECRET", "short"],
  ])("does not dispatch when %s prohibits execution", async (key, value) => {
    vi.stubEnv(key, value);
    expect((await runAiFilterRefreshSweep({ dependencies: deps })).status).toBe("off");
    expect(deps.claim).not.toHaveBeenCalled();
    vi.unstubAllEnvs();
  });

  it("does not resurrect disabled/deleted/replaced prompts while dispatching", async () => {
    deps.configure.mockRejectedValue(new AiFilterNotFoundError());
    expect(await runAiFilterRefreshSweep({ dependencies: deps })).toMatchObject({ started: 0, deferred: 1 });
    expect(deps.start).not.toHaveBeenCalled();
  });

  it("isolates dispatch failures so another watchlist still refreshes", async () => {
    deps.claim.mockReset().mockResolvedValue([])
      .mockResolvedValueOnce([target])
      .mockResolvedValueOnce([{ ...target, watchlistId: "another" }]);
    deps.start.mockRejectedValueOnce(new Error("dispatch unavailable"));
    expect(await runAiFilterRefreshSweep({ dependencies: deps })).toMatchObject({ started: 1, failed: 1 });
  });

  it("defers an expanded scope beyond 10k without executing", async () => {
    deps.assertScope.mockRejectedValue(new TypeError("ineligible"));
    expect(await runAiFilterRefreshSweep({ dependencies: deps })).toMatchObject({ deferred: 1, started: 0 });
    expect(deps.configure).not.toHaveBeenCalled();
  });
});


describe("refresh sweep fairness under a slow preparation", () => {
  it("claims only the next target before running out of its work budget", async () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(0);
    const claim = vi.fn().mockResolvedValue([target]);
    const configure = vi.fn().mockResolvedValue({ queryVersionId: "query" });
    const start = vi.fn().mockResolvedValue({ runId: "run" });
    const assertScope = vi.fn().mockImplementation(async () => { clock.mockReturnValue(45_001); return 1; });
    try {
      expect(await runAiFilterRefreshSweep({ dependencies: { claim, configure, start, assertScope } }))
        .toMatchObject({ claimed: 1, started: 1 });
      expect(claim).toHaveBeenCalledOnce();
      expect(claim).toHaveBeenCalledWith(expect.any(Date), 1);
    } finally { clock.mockRestore(); }
  });
});


it("prepares and starts each claimed target before checking the next scope", async () => {
  const events: string[] = [];
  deps.claim.mockReset().mockResolvedValue([])
    .mockResolvedValueOnce([target])
    .mockResolvedValueOnce([{ ...target, watchlistId: "second" }]);
  deps.assertScope.mockImplementation(async ({ watchlistId }) => { events.push(`scope:${watchlistId}`); return 1; });
  deps.start.mockImplementation(async ({ watchlistId }) => { events.push(`start:${watchlistId}`); return { runId: "run" }; });
  expect(await runAiFilterRefreshSweep({ dependencies: deps })).toMatchObject({ started: 2 });
  expect(events).toEqual(["scope:watchlist", "start:watchlist", "scope:second", "start:second"]);
  expect(deps.claim.mock.calls.every(([, limit]) => limit === 1)).toBe(true);
});
