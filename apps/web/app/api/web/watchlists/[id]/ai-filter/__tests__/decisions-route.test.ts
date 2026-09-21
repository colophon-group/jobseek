import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserIdFromHeaders: vi.fn(),
  listAiFilterDecisions: vi.fn(),
  moveAiFilterDecision: vi.fn(),
  reportAiFilterMistake: vi.fn(),
  undoAiFilterDecisionMove: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserIdFromHeaders: mocks.getSessionUserIdFromHeaders,
}));
vi.mock("@/lib/ai-filter/decision-service", () => ({
  listAiFilterDecisions: mocks.listAiFilterDecisions,
  moveAiFilterDecision: mocks.moveAiFilterDecision,
  reportAiFilterMistake: mocks.reportAiFilterMistake,
  undoAiFilterDecisionMove: mocks.undoAiFilterDecisionMove,
}));
vi.mock("@/lib/ai-filter/configuration-service", () => ({
  AiFilterNotFoundError: class AiFilterNotFoundError extends Error {},
  AiFilterEntitlementError: class AiFilterEntitlementError extends Error {},
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
}));

import { GET as listDecisions } from "../decisions/route";
import {
  DELETE as undoDecision,
  PATCH as moveDecision,
  POST as reportDecision,
} from "../decisions/[decisionId]/route";

const watchlistId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const decisionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const listContext = { params: Promise.resolve({ id: watchlistId }) };
const decisionContext = {
  params: Promise.resolve({ id: watchlistId, decisionId }),
};

describe("owner-only AI filter decision routes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserIdFromHeaders.mockResolvedValue("owner-1");
    mocks.listAiFilterDecisions.mockResolvedValue({
      decisions: [],
      nextOffset: 0,
      hasMore: false,
    });
    mocks.moveAiFilterDecision.mockResolvedValue({
      decision: "accepted",
      changed: true,
    });
    mocks.undoAiFilterDecisionMove.mockResolvedValue({
      decision: "rejected",
      changed: true,
    });
    mocks.reportAiFilterMistake.mockResolvedValue({ reported: true });
  });

  it("lists one bucket with bounded pagination and private caching", async () => {
    const response = await listDecisions(
      new Request(
        `https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions?bucket=accepted&offset=5&limit=10`,
      ),
      listContext,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(mocks.listAiFilterDecisions).toHaveBeenCalledWith(expect.objectContaining({
      ownerId: "owner-1",
      watchlistId,
      bucket: "accepted",
      offset: 5,
      limit: 10,
    }));
  });

  it("moves a decision with an idempotency key", async () => {
    const response = await moveDecision(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions/${decisionId}`, {
        method: "PATCH",
        body: JSON.stringify({
          decision: "accepted",
          idempotencyKey: "move-client-1",
        }),
      }),
      decisionContext,
    );

    expect(response.status).toBe(200);
    expect(mocks.moveAiFilterDecision).toHaveBeenCalledWith({
      ownerId: "owner-1",
      watchlistId,
      decisionId,
      to: "accepted",
      idempotencyKey: "move-client-1",
    });
  });

  it("undoes a move and reports a mistake through separate operations", async () => {
    const undoResponse = await undoDecision(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions/${decisionId}`, {
        method: "DELETE",
        body: JSON.stringify({
          moveIdempotencyKey: "move-client-1",
          idempotencyKey: "undo-client-1",
        }),
      }),
      decisionContext,
    );
    const reportResponse = await reportDecision(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions/${decisionId}`, {
        method: "POST",
        body: JSON.stringify({ idempotencyKey: "report-client-1" }),
      }),
      decisionContext,
    );

    expect(undoResponse.status).toBe(200);
    expect(reportResponse.status).toBe(200);
    expect(mocks.undoAiFilterDecisionMove).toHaveBeenCalledOnce();
    expect(mocks.reportAiFilterMistake).toHaveBeenCalledOnce();
  });

  it("returns an indistinguishable 404 before touching services when anonymous", async () => {
    mocks.getSessionUserIdFromHeaders.mockResolvedValue(null);
    const response = await moveDecision(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions/${decisionId}`, {
        method: "PATCH",
        body: JSON.stringify({
          decision: "accepted",
          idempotencyKey: "move-client-1",
        }),
      }),
      decisionContext,
    );

    expect(response.status).toBe(404);
    expect(mocks.moveAiFilterDecision).not.toHaveBeenCalled();
  });

  it("rejects unknown mutation fields", async () => {
    const response = await moveDecision(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions/${decisionId}`, {
        method: "PATCH",
        body: JSON.stringify({
          decision: "accepted",
          idempotencyKey: "move-client-1",
          extra: true,
        }),
      }),
      decisionContext,
    );

    expect(response.status).toBe(400);
    expect(mocks.moveAiFilterDecision).not.toHaveBeenCalled();
  });
});
