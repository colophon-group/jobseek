import { beforeEach, describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";
import type { SQL } from "drizzle-orm";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ transaction: vi.fn() }));

vi.mock("@/db", () => ({ db: { transaction: mocks.transaction } }));

import { PostgresAiFilterExecutionRepository } from "./postgres-repository";
import { aiFilterGlobalCache, aiFilterUsageLedger } from "@/db/schema";
import { normalizeClassifierInputV1 } from "./classifier-input";

beforeEach(() => vi.resetAllMocks());

describe("AI filter paid reservation authorization", () => {
  it("checks authorization in the database for every owner", async () => {
    const repository = new PostgresAiFilterExecutionRepository({
      user: null,
      project: null,
    });
    const input = {
      context: { ownerId: "otherOwner12345678" },
    } as Parameters<typeof repository.reserveBudget>[0];

    await repository.reserveBudget(input);
    expect(mocks.transaction).toHaveBeenCalledOnce();
  });

  it("refuses spend when the current query uses another classifier version", async () => {
    const where = vi.fn();
    const query = {
      from: () => query, innerJoin: () => query,
      where: (predicate: SQL) => { where(predicate); return query; },
      limit: async () => [],
    };
    const tx = { execute: vi.fn(), select: vi.fn(() => query), insert: vi.fn(), update: vi.fn() };
    mocks.transaction.mockImplementation(async callback => callback(tx));
    const repository = new PostgresAiFilterExecutionRepository({ user: null, project: null });
    await expect(repository.reserveBudget({
      context: { ownerId: "owner", watchlistId: "watchlist", queryVersionId: "stale-query" },
      now: new Date("2026-09-28T18:00:00Z"),
    } as Parameters<typeof repository.reserveBudget>[0])).rejects.toThrow("AI filter resource was not found");
    const predicate = new PgDialect().sqlToQuery(where.mock.calls[0][0] as SQL);
    for (const field of ["model", "prompt_version", "schema_version", "normalizer_version"]) {
      expect(predicate.sql).toContain(`"ai_filter_query_version"."${field}" =`);
    }
    expect(predicate.params).toEqual(expect.arrayContaining([
      "jev-1.13.0", "jev-job-fit-choice-v2", "classifier-input-v1", "classifier-input-normalizer-v4",
    ]));
    expect(tx.insert).not.toHaveBeenCalled();
    expect(tx.update).not.toHaveBeenCalled();
  });
});

describe("AI filter uncertain reservation recovery", () => {
  const now = new Date("2026-09-28T14:15:30Z");
  const candidateId = "11111111-1111-4111-8111-111111111111";
  const cacheKey = "a".repeat(64);
  const input = {
    context: {
      ownerId: "owner", watchlistId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      queryVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      segmentId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      queryText: "Robotics", leaseOwner: "retry-worker",
    },
    candidates: [{
      candidateId, cacheKey,
      postingFirstSeenAt: "2026-09-25T00:00:00Z",
      expiresAt: "2026-10-28T00:00:00Z",
      classifierInput: normalizeClassifierInputV1({
        candidateId, title: "Robotics Engineer", companyName: "Robotics Company",
        descriptionHtml: "<p>Build robots.</p>", selectedDescriptionLocale: "en",
      }),
    }],
    now,
  };
  const failedCache = {
    cacheKey, status: "failed", decision: null,
    failureCode: "invalid_request", updatedAt: new Date("2026-09-28T13:51:03Z"),
    leaseOwner: null as string | null, leaseExpiresAt: null as Date | null,
    expiresAt: new Date("2026-10-28T00:00:00Z"),
  };
  const oldCharge = {
    id: "historical-charge", status: "uncertain", ownerId: "owner",
    monthStart: new Date("2026-09-01T00:00:00Z"), reservedNanodollars: 6_720_000,
    reconciledAt: new Date("2026-09-28T13:44:24Z"),
  };

  function setup(
    cache = failedCache,
    ledger = oldCharge,
    reclaimed: { cacheKey: string }[] = [{ cacheKey }],
    candidates = input.candidates,
    lockedCache = cache,
  ) {
    const orderBy = vi.fn();
    const lockOrder: unknown[] = [];
    const rows: unknown[][] = [
      [], candidates.map(candidate => ({ ...cache, cacheKey: candidate.cacheKey })),
      ...candidates.flatMap(candidate => [
        [ledger], [{ ...lockedCache, cacheKey: candidate.cacheKey }],
      ]),
    ];
    const select = vi.fn(() => {
      const result = rows.shift()!;
      let table: unknown;
      const query = {
        from: vi.fn((value: unknown) => { table = value; return query; }), where: vi.fn(() => query),
        orderBy: vi.fn((...args: SQL[]) => { orderBy(...args); return query; }),
        for: vi.fn((mode: string) => { expect(mode).toBe("update"); lockOrder.push(table); return query; }),
        limit: vi.fn(async () => result),
        then: (resolve: (value: unknown[]) => unknown) => Promise.resolve(result).then(resolve),
      };
      return query;
    });
    const updateQuery = {
      set: vi.fn(() => updateQuery), where: vi.fn(() => updateQuery),
      returning: vi.fn(async () => reclaimed),
    };
    const tx = { select, update: vi.fn(() => updateQuery) };
    mocks.transaction.mockImplementation(async (callback) => callback(tx));
    return { tx, orderBy, updateQuery, lockOrder };
  }

  it("reclaims the original five-job failure history without changing its charged ledger", async () => {
    // 13:44 ambiguous request charged; 13:51 retry released its new reservation
    // and changed the cache to invalid_request. The historical uncertain row
    // remains, and must not block the next retry forever.
    const candidates = Array.from({ length: 5 }, (_, index) => {
      const id = `11111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}`;
      return {
        ...input.candidates[0]!, candidateId: id, cacheKey: String(index + 1).repeat(64),
        classifierInput: normalizeClassifierInputV1({
          candidateId: id, title: "Robotics Engineer", companyName: "Robotics Company",
          descriptionHtml: "<p>Build robots.</p>", selectedDescriptionLocale: "en",
        }),
      };
    });
    const { tx, orderBy } = setup(failedCache, oldCharge, undefined, candidates);
    const repository = new PostgresAiFilterExecutionRepository({ user: null, project: null });
    const result = await repository.resolve({ ...input, candidates });

    expect(result.claims).toEqual(candidates);
    expect(result.waitingCacheKeys).toEqual([]);
    expect(tx.update.mock.calls).toEqual(candidates.map(() => [aiFilterGlobalCache]));
    const dialect = new PgDialect();
    expect(orderBy.mock.calls[0].map((part: SQL) => dialect.sqlToQuery(part).sql)).toEqual([
      '"ai_filter_usage_ledger"."status" = \'reserved\' desc',
      '"ai_filter_usage_ledger"."created_at" desc',
    ]);
  });

  it("does not bypass the cooldown for a recently charged uncertain reservation", async () => {
    const { tx } = setup(failedCache, { ...oldCharge, reconciledAt: new Date(now.getTime() - 60_000) });
    const result = await new PostgresAiFilterExecutionRepository({ user: null, project: null }).resolve(input);
    expect(result.claims).toEqual([]);
    expect(result.waitingCacheKeys).toEqual([cacheKey]);
    expect(tx.update).not.toHaveBeenCalled();
  });

  it("does not bypass a live reserved ledger with a historical charge", async () => {
    const { tx } = setup(failedCache, { ...oldCharge, status: "reserved" });
    const result = await new PostgresAiFilterExecutionRepository({ user: null, project: null }).resolve(input);
    expect(result.claims).toEqual([]);
    expect(result.waitingCacheKeys).toEqual([cacheKey]);
    expect(tx.update).not.toHaveBeenCalled();
  });

  it("does not touch an active cache lease even with an old uncertain charge", async () => {
    const { tx } = setup({ ...failedCache, status: "pending", leaseExpiresAt: new Date(now.getTime() + 60_000) });
    const result = await new PostgresAiFilterExecutionRepository({ user: null, project: null }).resolve(input);
    expect(result.claims).toEqual([]);
    expect(result.waitingCacheKeys).toEqual([cacheKey]);
    expect(tx.select).toHaveBeenCalledTimes(2);
    expect(tx.update).not.toHaveBeenCalled();
  });

  it("waits when another worker wins the atomic cache claim", async () => {
    setup(failedCache, oldCharge, []);
    const result = await new PostgresAiFilterExecutionRepository({ user: null, project: null }).resolve(input);
    expect(result.claims).toEqual([]);
    expect(result.waitingCacheKeys).toEqual([cacheKey]);
  });

  it("does not charge a new reservation using a stale expired cache snapshot", async () => {
    const expired = {
      ...failedCache, status: "pending", leaseOwner: "expired-worker",
      leaseExpiresAt: new Date(now.getTime() - 60_000),
    };
    const active = {
      ...expired, leaseOwner: "new-worker", leaseExpiresAt: new Date(now.getTime() + 60_000),
    };
    const { tx, lockOrder } = setup(expired, { ...oldCharge, status: "reserved" }, undefined, input.candidates, active);
    const result = await new PostgresAiFilterExecutionRepository({ user: null, project: null }).resolve(input);
    expect(result.claims).toEqual([]);
    expect(result.waitingCacheKeys).toEqual([cacheKey]);
    expect(tx.select).toHaveBeenCalledTimes(4);
    expect(lockOrder).toEqual([aiFilterUsageLedger, aiFilterGlobalCache]);
    expect(tx.update).not.toHaveBeenCalled();
  });
});
