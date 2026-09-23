import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserIdFromHeaders: vi.fn(),
  getAiFilterOwnerState: vi.fn(),
  assertAiFilterEntitlement: vi.fn(),
  putAiFilterConfiguration: vi.fn(),
  disableAiFilterConfiguration: vi.fn(),
  assertAiFilterCandidateScope: vi.fn(),
  startAiFilterCatchup: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserIdFromHeaders: mocks.getSessionUserIdFromHeaders,
}));
vi.mock("@/lib/ai-filter/configuration-service", () => ({
  AiFilterNotFoundError: class AiFilterNotFoundError extends Error {},
  AiFilterEntitlementError: class AiFilterEntitlementError extends Error {},
  getAiFilterOwnerState: mocks.getAiFilterOwnerState,
  assertAiFilterEntitlement: mocks.assertAiFilterEntitlement,
  putAiFilterConfiguration: mocks.putAiFilterConfiguration,
  disableAiFilterConfiguration: mocks.disableAiFilterConfiguration,
}));
vi.mock("@/lib/ai-filter/postgres-repository", () => ({
  AiFilterAuthorizationError: class AiFilterAuthorizationError extends Error {},
}));
vi.mock("@/lib/ai-filter/candidate-loader", () => ({
  assertAiFilterCandidateScope: mocks.assertAiFilterCandidateScope,
  AiFilterCandidateLoadError: class AiFilterCandidateLoadError extends Error {
    code: string;
    constructor(code: string) {
      super(code);
      this.code = code;
    }
  },
}));
vi.mock("@/lib/ai-filter/workflow-trigger", () => ({
  startAiFilterCatchup: mocks.startAiFilterCatchup,
}));

import { AiFilterNotFoundError } from "@/lib/ai-filter/configuration-service";
import { DELETE, GET, PUT } from "../route";

const watchlistId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const context = { params: Promise.resolve({ id: watchlistId }) };
const state = {
  watchlistId,
  enabled: true,
  entitled: true,
  query: "remote Rust",
  queryRevision: 1,
  queryVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  status: "processing",
  counts: { accepted: 0, rejected: 0, total: 0 },
  budget: { actualNanodollars: 0, reservedNanodollars: 0 },
  lastCaughtUpAt: null,
  latestEventSequence: 0,
};

describe("owner-only AI filter configuration route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserIdFromHeaders.mockResolvedValue("owner-1");
    mocks.getAiFilterOwnerState.mockResolvedValue(state);
    mocks.assertAiFilterEntitlement.mockResolvedValue(undefined);
    mocks.putAiFilterConfiguration.mockResolvedValue(state);
    mocks.disableAiFilterConfiguration.mockResolvedValue(undefined);
    mocks.assertAiFilterCandidateScope.mockResolvedValue(500);
    mocks.startAiFilterCatchup.mockResolvedValue({ runId: "run-1" });
  });

  it("uses identical not-found behavior for an anonymous caller", async () => {
    mocks.getSessionUserIdFromHeaders.mockResolvedValue(null);
    const response = await GET(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter`),
      context,
    );
    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ error: "not_found" });
    expect(mocks.getAiFilterOwnerState).not.toHaveBeenCalled();
  });

  it("reads durable state without starting classification", async () => {
    const response = await GET(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter`),
      context,
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(mocks.getAiFilterOwnerState).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
    });
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });

  it("saves one query without evaluating historical jobs", async () => {
    const response = await PUT(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query: "remote Rust" }),
      }),
      context,
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ state, workflow: null });
    expect(mocks.putAiFilterConfiguration).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
      query: "remote Rust",
    });
    expect(mocks.assertAiFilterEntitlement).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
    });
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });

  it("does not start a workflow when owner authorization fails", async () => {
    mocks.assertAiFilterEntitlement.mockRejectedValue(new AiFilterNotFoundError());
    const response = await PUT(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter`, {
        method: "PUT",
        body: JSON.stringify({ query: "remote Rust" }),
      }),
      context,
    );
    expect(response.status).toBe(404);
    expect(mocks.assertAiFilterCandidateScope).not.toHaveBeenCalled();
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });

  it("disables processing without starting replacement work", async () => {
    const response = await DELETE(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter`, {
        method: "DELETE",
      }),
      context,
    );
    expect(response.status).toBe(204);
    expect(mocks.disableAiFilterConfiguration).toHaveBeenCalledOnce();
    expect(mocks.startAiFilterCatchup).not.toHaveBeenCalled();
  });
});
