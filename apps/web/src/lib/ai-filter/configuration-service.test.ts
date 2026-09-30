import { createHash } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ transaction: vi.fn(), entitlement: vi.fn() }));
vi.mock("@/db", () => ({ db: { transaction: mocks.transaction } }));
vi.mock("@/lib/paid-entitlement", () => ({
  hasPaidEntitlement: mocks.entitlement, paidEntitlementCondition: vi.fn(),
}));

import { putAiFilterConfiguration } from "./configuration-service";
import { aiFilterConfiguration, aiFilterEvent, aiFilterQueryVersion, aiFilterSegment, watchlist, watchlistCompany } from "@/db/schema";
import { AI_FILTER_PROMPT_VERSION, JEV_MODEL } from "./policy";
import { CLASSIFIER_INPUT_NORMALIZER_VERSION, CLASSIFIER_INPUT_SCHEMA_VERSION } from "./classifier-input";

const now = new Date("2026-09-28T18:00:00Z");
const versions = {
  model: JEV_MODEL, promptVersion: AI_FILTER_PROMPT_VERSION,
  schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION, normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
};
const input = { ownerId: "owner", watchlistId: "watchlist", query: "Robotics", candidateLanguages: [], now };

function setup(overrides: Record<string, unknown> = {}) {
  const configuration = { id: "configuration", status: "enabled", currentRevision: 3, disabledAt: null, lastCaughtUpAt: now };
  const oldQuery = {
    id: "old-query", configurationId: configuration.id, revision: 3,
    queryText: "Robotics", normalizedQuery: "Robotics", ...versions,
    filterFingerprint: createHash("sha256").update('{"candidateLanguages":[],"companyIds":["company"],"filters":{}}').digest("hex"),
    candidateLanguages: [], horizonStartedAt: new Date("2000-01-01T00:00:00Z"), horizonEndsAt: now,
    ...overrides,
  };
  const queries: Record<string, unknown>[] = [oldQuery];
  const writes: { table: unknown; value: Record<string, unknown> }[] = [];
  const tx = {
    execute: vi.fn(),
    select: vi.fn(() => {
      let rows: unknown[] = [];
      const query = {
        from(table: unknown) {
          if (table === watchlist) rows = [{ id: input.watchlistId, filters: {} }];
          else if (table === watchlistCompany) rows = [{ companyId: "company" }];
          else if (table === aiFilterConfiguration) rows = [configuration];
          else if (table === aiFilterQueryVersion) rows = [queries.at(-1)];
          else throw new Error("Unexpected table");
          return query;
        },
        where: () => query,
        limit: async () => rows,
        then: (resolve: (value: unknown[]) => unknown) => Promise.resolve(rows).then(resolve),
      };
      return query;
    }),
    insert: vi.fn((table: unknown) => ({ values: async (value: Record<string, unknown>) => {
      writes.push({ table, value });
      if (table === aiFilterQueryVersion) queries.push(value);
    } })),
    update: vi.fn((table: unknown) => ({ set: (value: Record<string, unknown>) => ({ where: async () => {
      writes.push({ table, value });
      if (table === aiFilterConfiguration) Object.assign(configuration, value);
      if (table === aiFilterQueryVersion) Object.assign(queries.at(-1)!, value);
    } }) })),
  };
  let transactionCount = 0;
  // Exercise the mutation transaction; the separate owner-state read is covered
  // by route/read tests and is not part of the migration contract under test.
  mocks.transaction.mockImplementation(async (callback) => ++transactionCount % 2 ? callback(tx) : { queryRevision: configuration.currentRevision });
  mocks.entitlement.mockResolvedValue(true);
  return { oldQuery, configuration, queries, writes };
}

beforeEach(() => vi.resetAllMocks());

describe("AI filter classifier version migration", () => {
  it.each(Object.keys(versions))("refreshes unchanged text for stale %s once, preserving history", async (field) => {
    const state = setup({ [field]: "previous-version" });
    const historical = structuredClone(state.oldQuery);
    await putAiFilterConfiguration(input);
    expect(state.queries).toHaveLength(2);
    expect(state.queries[1]).toMatchObject({
      ...versions, queryText: "Robotics", normalizedQuery: "Robotics", revision: 4,
      horizonStartedAt: new Date("2000-01-01T00:00:00Z"), horizonEndsAt: new Date("2026-09-28T18:00:01Z"),
    });
    expect(state.oldQuery).toEqual(historical);
    expect(state.configuration).toMatchObject({ currentRevision: 4, lastCaughtUpAt: null, status: "enabled" });
    expect(state.writes.find(write => write.table === aiFilterSegment)?.value).toMatchObject({
      status: "cancelled", stopReason: "configuration_changed", leaseOwner: null, leaseExpiresAt: null,
    });
    expect(state.writes.find(write => write.table === aiFilterEvent)?.value).toMatchObject({
      type: "query_changed", payload: { revision: 4, reason: "classifier_version_changed" },
    });
    // No decision, cache, usage-ledger or budget-account writes are permitted.
    const allowedTables: unknown[] = [aiFilterQueryVersion, aiFilterConfiguration, aiFilterSegment, aiFilterEvent];
    expect(state.writes.every(write => allowedTables.includes(write.table))).toBe(true);
    const writeCount = state.writes.length;
    await putAiFilterConfiguration(input);
    expect(state.queries).toHaveLength(2);
    expect(state.writes).toHaveLength(writeCount);
  });

  it("reuses an unchanged current-version query without rewriting its history", async () => {
    const state = setup();
    await putAiFilterConfiguration(input);
    expect(state.queries).toHaveLength(1);
    expect(state.writes).toEqual([]);
  });
});
