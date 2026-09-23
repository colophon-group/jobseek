import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getSessionUserId: vi.fn(),
  assertAiFilterEntitlement: vi.fn(),
  createWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  putAiFilterConfiguration: vi.fn(),
  disableAiFilterConfiguration: vi.fn(),
  assertAiFilterCandidateScope: vi.fn(),
  logExternalError: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: () => mocks.getSessionUserId(),
}));

vi.mock("@/lib/services/watchlists", () => ({
  createWatchlist: (...args: unknown[]) => mocks.createWatchlist(...args),
  deleteWatchlist: (...args: unknown[]) => mocks.deleteWatchlist(...args),
}));

vi.mock("@/lib/ai-filter/configuration-service", () => ({
  AiFilterEntitlementError: class AiFilterEntitlementError extends Error {},
  assertAiFilterEntitlement: (...args: unknown[]) =>
    mocks.assertAiFilterEntitlement(...args),
  putAiFilterConfiguration: (...args: unknown[]) =>
    mocks.putAiFilterConfiguration(...args),
  disableAiFilterConfiguration: (...args: unknown[]) =>
    mocks.disableAiFilterConfiguration(...args),
}));

vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: (...args: unknown[]) => mocks.logExternalError(...args),
}));
vi.mock("@/lib/ai-filter/candidate-loader", () => ({
  assertAiFilterCandidateScope: (...args: unknown[]) =>
    mocks.assertAiFilterCandidateScope(...args),
}));

import { AiFilterEntitlementError } from "@/lib/ai-filter/configuration-service";
import {
  configureAiFilter,
  createAiFilteredWatchlist,
  disableAiFilter,
} from "../ai-filter";

const watchlistId = "11111111-1111-4111-8111-111111111111";
const draft = {
  title: "Backend",
  companyIds: [],
  filters: { anyCompany: true },
  isPublic: false as const,
};
const configuredState = {
  watchlistId,
  enabled: true,
  entitled: true,
  query: "Backend developer tools",
  queryRevision: 1,
  queryVersionId: "22222222-2222-4222-8222-222222222222",
  status: "idle" as const,
  counts: { accepted: 0, rejected: 0, total: 0 },
  progress: { selectionOffset: 0, scannedCount: 0, completedCount: 0, stopReason: null },
  lastCaughtUpAt: null,
  latestEventSequence: 0,
};

describe("AI filter watchlist actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionUserId.mockResolvedValue("user-1");
    mocks.assertAiFilterEntitlement.mockResolvedValue(undefined);
    mocks.createWatchlist.mockResolvedValue({ id: watchlistId, slug: "backend" });
    mocks.deleteWatchlist.mockResolvedValue({ ok: true });
    mocks.putAiFilterConfiguration.mockResolvedValue(configuredState);
    mocks.disableAiFilterConfiguration.mockResolvedValue(undefined);
    mocks.assertAiFilterCandidateScope.mockResolvedValue(24);
  });

  it("creates and configures the watchlist before returning its destination", async () => {
    await expect(createAiFilteredWatchlist({
      draft,
      query: "Backend developer tools",
    })).resolves.toEqual({ id: watchlistId, slug: "backend" });

    expect(mocks.createWatchlist).toHaveBeenCalledWith(draft);
    expect(mocks.assertAiFilterEntitlement).toHaveBeenCalledWith({
      ownerId: "user-1",
    });
    expect(mocks.assertAiFilterCandidateScope).toHaveBeenCalledWith({
      ownerId: "user-1",
      watchlistId,
    });
    expect(mocks.putAiFilterConfiguration).toHaveBeenCalledWith({
      ownerId: "user-1",
      watchlistId,
      query: "Backend developer tools",
    });
    expect(mocks.deleteWatchlist).not.toHaveBeenCalled();
  });

  it("rolls back the new watchlist when configuration cannot be attached", async () => {
    mocks.putAiFilterConfiguration.mockRejectedValue(
      new AiFilterEntitlementError(),
    );

    await expect(createAiFilteredWatchlist({
      draft,
      query: "Backend developer tools",
    })).resolves.toEqual({ error: "subscription_required" });

    expect(mocks.deleteWatchlist).toHaveBeenCalledWith(watchlistId);
  });

  it("rejects an ineligible account before creating or counting candidates", async () => {
    mocks.assertAiFilterEntitlement.mockRejectedValue(
      new AiFilterEntitlementError(),
    );

    await expect(createAiFilteredWatchlist({
      draft,
      query: "Backend developer tools",
    })).resolves.toEqual({ error: "subscription_required" });

    expect(mocks.createWatchlist).not.toHaveBeenCalled();
    expect(mocks.assertAiFilterCandidateScope).not.toHaveBeenCalled();
  });

  it("configures an existing owned watchlist without creating another one", async () => {
    await expect(configureAiFilter(
      watchlistId,
      "Backend developer tools",
    )).resolves.toEqual({ ok: true, state: configuredState });

    expect(mocks.putAiFilterConfiguration).toHaveBeenCalledWith({
      ownerId: "user-1",
      watchlistId,
      query: "Backend developer tools",
    });
    expect(mocks.assertAiFilterCandidateScope).toHaveBeenCalledWith({
      ownerId: "user-1",
      watchlistId,
    });
    expect(mocks.assertAiFilterEntitlement).toHaveBeenCalledWith({
      ownerId: "user-1",
      watchlistId,
    });
    expect(mocks.createWatchlist).not.toHaveBeenCalled();
  });

  it("disables matching on an existing owned watchlist", async () => {
    await expect(disableAiFilter(watchlistId)).resolves.toEqual({ ok: true });

    expect(mocks.disableAiFilterConfiguration).toHaveBeenCalledWith({
      ownerId: "user-1",
      watchlistId,
    });
  });
});
