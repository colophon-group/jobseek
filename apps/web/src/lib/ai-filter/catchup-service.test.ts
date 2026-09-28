import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ transaction: vi.fn(), entitlement: vi.fn(), load: vi.fn(), execute: vi.fn() }));
vi.mock("@/db", () => ({ db: { transaction: mocks.transaction } }));
vi.mock("@/lib/paid-entitlement", () => ({ hasPaidEntitlement: mocks.entitlement }));
vi.mock("./candidate-loader", () => ({ loadAiFilterCandidatePage: mocks.load, AiFilterCandidateLoadError: class extends Error {} }));
vi.mock("./orchestrator", () => ({ executeAiFilterSegment: mocks.execute }));
vi.mock("./mining-policy", () => ({ assertAiFilterMiningAllowed: vi.fn() }));

import { runAiFilterCatchupStep } from "./catchup-service";
import { AI_FILTER_PROMPT_VERSION, JEV_MODEL } from "./policy";
import { CLASSIFIER_INPUT_NORMALIZER_VERSION, CLASSIFIER_INPUT_SCHEMA_VERSION } from "./classifier-input";

beforeEach(() => vi.resetAllMocks());

describe("durable catch-up classifier version boundary", () => {
  const versions = { model: JEV_MODEL, promptVersion: AI_FILTER_PROMPT_VERSION, schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION, normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION };
  it.each(Object.keys(versions))("stops a resumed stale %s revision before loading or classifying jobs", async field => {
    const current = { ...versions, [field]: "old-version", configurationStatus: "enabled", queryVersionId: "old-query" };
    const selectQuery = { from: () => selectQuery, innerJoin: () => selectQuery, where: () => selectQuery, limit: async () => [current] };
    const where = vi.fn();
    const set = vi.fn(() => ({ where }));
    const tx = { execute: vi.fn(), select: vi.fn(() => selectQuery), update: vi.fn(() => ({ set })) };
    mocks.transaction.mockImplementation(async callback => callback(tx));
    mocks.entitlement.mockResolvedValue(true);
    const result = await runAiFilterCatchupStep({ ownerId: "owner", watchlistId: "watchlist", leaseOwner: "new-worker", demandTargetOffset: 50 });
    expect(result).toEqual({ status: "disabled", segmentId: null });
    expect(mocks.load).not.toHaveBeenCalled();
    expect(mocks.execute).not.toHaveBeenCalled();
    expect(tx.select).toHaveBeenCalledOnce();
    expect(tx.update).not.toHaveBeenCalled();
  });
});
