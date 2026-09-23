import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserIdFromHeaders: vi.fn(),
  getAiFilterOwnerState: vi.fn(),
  putAiFilterConfiguration: vi.fn(),
  startAiFilterCatchup: vi.fn(),
  assertAiFilterCandidateScope: vi.fn(),
  limit: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserIdFromHeaders: mocks.getSessionUserIdFromHeaders,
}));
vi.mock("@/lib/ai-filter/configuration-service", () => ({
  AiFilterNotFoundError: class AiFilterNotFoundError extends Error {},
  AiFilterEntitlementError: class AiFilterEntitlementError extends Error {},
  getAiFilterOwnerState: mocks.getAiFilterOwnerState,
  putAiFilterConfiguration: mocks.putAiFilterConfiguration,
}));
vi.mock("@/lib/ai-filter/postgres-repository", () => ({
  AiFilterAuthorizationError: class AiFilterAuthorizationError extends Error {},
}));
vi.mock("@/lib/ai-filter/candidate-loader", () => ({
  AiFilterCandidateLoadError: class AiFilterCandidateLoadError extends Error {
    code: string;
    constructor(code: string) {
      super(code);
      this.code = code;
    }
  },
  assertAiFilterCandidateScope: mocks.assertAiFilterCandidateScope,
}));
vi.mock("@/lib/ai-filter/workflow-trigger", () => ({
  startAiFilterCatchup: mocks.startAiFilterCatchup,
}));
vi.mock("@/lib/rate-limit", () => ({
  aiFilterDemandLimiter: { limit: mocks.limit },
}));

import { POST } from "../reconcile/route";

const watchlistId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const context = { params: Promise.resolve({ id: watchlistId }) };
const state = {
  watchlistId,
  enabled: true,
  entitled: true,
  query: "remote Rust",
  queryRevision: 1,
  queryVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  status: "idle",
  counts: { accepted: 0, rejected: 0, total: 0 },
  progress: {
    selectionOffset: 0,
    scannedCount: 0,
    completedCount: 0,
    stopReason: null,
  },
  budget: { actualNanodollars: 0, reservedNanodollars: 0 },
  lastCaughtUpAt: null,
  latestEventSequence: 0,
};

function request(offset: number) {
  return new Request(
    `https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/reconcile`,
    { method: "POST", body: JSON.stringify({ offset }) },
  );
}

function scopedRequest(
  offset: number,
  jobLanguages: readonly string[],
  locale = "en",
) {
  return new Request(
    `https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/reconcile`,
    {
      method: "POST",
      body: JSON.stringify({ offset, jobLanguages, locale }),
    },
  );
}

describe("scroll-demand AI filter reconciliation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserIdFromHeaders.mockResolvedValue("owner-1");
    mocks.getAiFilterOwnerState.mockResolvedValue(state);
    mocks.putAiFilterConfiguration.mockResolvedValue(state);
    mocks.startAiFilterCatchup.mockResolvedValue({ runId: "run-1" });
    mocks.assertAiFilterCandidateScope.mockResolvedValue(1_000);
    mocks.limit.mockResolvedValue({ success: true, reset: Date.now() + 60_000 });
  });

  it("refreshes the hard-filter scope and requests a selective-feed runway", async () => {
    const response = await POST(request(20), context);

    expect(response.status).toBe(202);
    expect(mocks.putAiFilterConfiguration).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
      query: "remote Rust",
    });
    expect(mocks.assertAiFilterCandidateScope).toHaveBeenCalledWith(
      expect.objectContaining({ ownerId: "owner-1", watchlistId }),
    );
    expect(mocks.startAiFilterCatchup).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
      demandTargetOffset: 500,
    });
  });

  it("clamps a forged future cursor to the persisted frontier", async () => {
    const response = await POST(request(10_000), context);

    expect(response.status).toBe(202);
    expect(mocks.startAiFilterCatchup).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
      demandTargetOffset: 500,
    });
  });

  it("rejects expired entitlement before the external candidate count", async () => {
    mocks.getAiFilterOwnerState.mockResolvedValue({ ...state, entitled: false });

    const response = await POST(request(0), context);

    expect(response.status).toBe(403);
    expect(mocks.assertAiFilterCandidateScope).not.toHaveBeenCalled();
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });

  it("limits replay before the external count or workflow start", async () => {
    mocks.limit.mockResolvedValue({
      success: false,
      reset: Date.now() + 60_000,
    });

    const response = await POST(request(0), context);

    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBeTruthy();
    expect(mocks.assertAiFilterCandidateScope).not.toHaveBeenCalled();
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });

  it("snapshots the visible all-language scope into the immutable query revision", async () => {
    const response = await POST(scopedRequest(20, ["*"]), context);

    expect(response.status).toBe(202);
    expect(mocks.putAiFilterConfiguration).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
      query: "remote Rust",
      candidateLanguages: [],
    });
  });

  it("does not restart work for an already prefetched range", async () => {
    mocks.putAiFilterConfiguration.mockResolvedValue({
      ...state,
      progress: { ...state.progress, selectionOffset: 500, scannedCount: 50 },
    });

    const response = await POST(request(20), context);

    expect(response.status).toBe(200);
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
    await expect(response.json()).resolves.toMatchObject({ workflow: null });
  });

  it("rejects demand beyond the eligible search ceiling", async () => {
    const response = await POST(request(10_001), context);

    expect(response.status).toBe(400);
    expect(mocks.getAiFilterOwnerState).not.toHaveBeenCalled();
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });
});
