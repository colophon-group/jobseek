import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSessionUserIdFromHeaders: vi.fn(),
  listAiFilterDecisions: vi.fn(),
  listSharedAiFilterDecisions: vi.fn(),
  getAiFilterOwnerState: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserIdFromHeaders: mocks.getSessionUserIdFromHeaders,
}));
vi.mock("@/lib/ai-filter/decision-service", () => ({
  listAiFilterDecisions: mocks.listAiFilterDecisions,
  listSharedAiFilterDecisions: mocks.listSharedAiFilterDecisions,
}));
vi.mock("@/lib/ai-filter/configuration-service", () => ({
  AiFilterNotFoundError: class AiFilterNotFoundError extends Error {},
  AiFilterEntitlementError: class AiFilterEntitlementError extends Error {},
  getAiFilterOwnerState: mocks.getAiFilterOwnerState,
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
import { AiFilterNotFoundError } from "@/lib/ai-filter/configuration-service";
const watchlistId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const listContext = { params: Promise.resolve({ id: watchlistId }) };

describe("AI filter accepted-result route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserIdFromHeaders.mockResolvedValue("owner-1");
    mocks.listAiFilterDecisions.mockResolvedValue({
      decisions: [],
      nextOffset: 0,
      hasMore: false,
    });
    mocks.listSharedAiFilterDecisions.mockResolvedValue({
      decisions: [],
      nextOffset: 0,
      hasMore: false,
    });
    mocks.getAiFilterOwnerState.mockResolvedValue({
      watchlistId,
      enabled: true,
      status: "caught_up",
    });
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

  it("marks anonymous shared reads for the server-side 20-job cap", async () => {
    mocks.getSessionUserIdFromHeaders.mockResolvedValue(null);

    const response = await listDecisions(
      new Request(
        `https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions?bucket=accepted&offset=20&limit=20`,
      ),
      listContext,
    );

    expect(response.status).toBe(200);
    expect(mocks.listAiFilterDecisions).not.toHaveBeenCalled();
    expect(mocks.listSharedAiFilterDecisions).toHaveBeenCalledWith(expect.objectContaining({
      watchlistId,
      anonymous: true,
      offset: 20,
      limit: 20,
    }));
  });

  it("falls back to shared accepted results for a signed-in non-owner", async () => {
    mocks.listAiFilterDecisions.mockRejectedValueOnce(new AiFilterNotFoundError());

    const response = await listDecisions(
      new Request(
        `https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions?bucket=accepted&offset=0&limit=20`,
      ),
      listContext,
    );

    expect(response.status).toBe(200);
    expect(mocks.listSharedAiFilterDecisions).toHaveBeenCalledWith(expect.objectContaining({
      watchlistId,
      offset: 0,
      limit: 20,
    }));
  });

  it("does not expose a rejected-results bucket", async () => {
    const response = await listDecisions(
      new Request(`https://jseek.co/api/web/watchlists/${watchlistId}/ai-filter/decisions?bucket=rejected`),
      listContext,
    );
    expect(response.status).toBe(400);
    expect(mocks.listAiFilterDecisions).not.toHaveBeenCalled();
  });
});
