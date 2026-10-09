// @vitest-environment node
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import type { Sql } from "postgres";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({ client: null as Sql | null, postings: [] as Array<{ id: string; firstSeenAt: string }>, classify: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("@/db", async () => {
  if (!process.env.AI_FILTER_TEST_DATABASE_URL) return { db: {} };
  const uri = new URL(process.env.AI_FILTER_TEST_DATABASE_URL);
  if (!["localhost", "127.0.0.1"].includes(uri.hostname) || !uri.pathname.endsWith("_fixture")) {
    throw new Error("AI filter tests require an explicit localhost *_fixture database");
  }
  const { default: postgres } = await import("postgres");
  const { drizzle } = await import("drizzle-orm/postgres-js");
  const schema = await import("@/db/schema");
  mocks.client = postgres(uri.toString(), { max: 5, prepare: false, connection: { TimeZone: "UTC" }, onnotice: () => {} });
  return { db: drizzle(mocks.client, { schema }) };
});
vi.mock("@/lib/services/search", () => ({ getPostingDetail: async () => null }));
vi.mock("@/lib/services/watchlist-matcher", () => ({
  compileWatchlistMatcherSources: async (sources: unknown[]) => sources.map(() => ({ candidateFilters: { anyCompany: true, companyIds: [] } })),
  readWatchlistCandidatesByIds: async (ids: string[]) => mocks.postings.filter(post => ids.includes(post.id)),
  readWatchlistCandidates: async (input: { excludePostingIds?: string[]; window?: { windowEnd: Date }; limit: number; offset: number }) => {
    const posts = mocks.postings.filter(post =>
      !input.excludePostingIds?.includes(post.id) &&
      (!input.window || Date.parse(post.firstSeenAt) < input.window.windowEnd.getTime())
    ).sort((a, b) => Date.parse(b.firstSeenAt) - Date.parse(a.firstSeenAt));
    return { total: posts.length, postings: posts.slice(input.offset, input.offset + input.limit).map(post => ({
      ...post, title: "AI Engineer", company: { id: "company", name: "Fixture", slug: "fixture", icon: null }, sourceUrl: "https://example.test/job", locationNames: [], isActive: true,
      classifierMetadata: { locations: [{ name: "Zurich", type: "onsite" }], employmentType: "full_time", seniorityName: null, experienceMin: null, experienceMax: null, technologies: [], salaryMin: null, salaryMax: null, salaryCurrency: null, salaryPeriod: null, descriptionLocale: "en" },
    })) };
  },
}));
vi.mock("./mining-policy", () => ({ assertAiFilterMiningAllowed: async () => {} }));
vi.mock("./jev-client", async importOriginal => {
  const actual = await importOriginal<typeof import("./jev-client")>();
  return { ...actual, JevClient: class { classify = mocks.classify; } };
});
vi.mock("./workflow-trigger", () => ({ startAiFilterCatchup: vi.fn() }));

import { runAiFilterCatchupStep } from "./catchup-service";
import { claimAiFilterRefreshTargets } from "./refresh-service";
import { getAiFilterOwnerState, putAiFilterConfiguration } from "./configuration-service";
import { listAiFilterDecisions } from "./decision-service";
import { AI_FILTER_PROMPT_VERSION, JEV_MODEL } from "./policy";

withTestEnv({ VERCEL_ENV: "production", AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true", TYPESAFE_AI_TOKEN: "fixture", AI_FILTER_CACHE_HMAC_SECRET: "x".repeat(32), PADDLE_ENVIRONMENT: undefined, STRIPE_ENVIRONMENT: undefined });
const sql = () => mocks.client!;
beforeAll(async () => {
  if (!mocks.client) return;
  await sql().unsafe(`DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;
    CREATE TABLE "user" (id text PRIMARY KEY);
    CREATE TABLE watchlist (id uuid PRIMARY KEY, user_id text REFERENCES "user"(id), title text DEFAULT 'AI', filters jsonb DEFAULT '{"anyCompany":true}');
    CREATE TABLE watchlist_company (watchlist_id uuid, company_id uuid);
    CREATE TABLE user_preferences (user_id text, locale text DEFAULT 'en', job_languages text[] DEFAULT '{}');
    CREATE TABLE subscription (user_id text, plan text, status text, ends_at timestamptz);`);
  for (const file of ["0092_jev_ai_filter_foundation.sql", "0093_ai_filter_historical_horizon.sql", "0094_ai_filter_candidate_languages.sql", "0103_ai_filter_freshness.sql"]) {
    for (const statement of (await readFile(`drizzle/${file}`, "utf8")).split("--> statement-breakpoint").filter(s => s.trim())) {
      await sql().unsafe(statement);
    }
  }
});
afterAll(async () => { await mocks.client?.end(); });

async function fixture(ownerId = "owner") {
  const watchlistId = randomUUID();
  await sql()`INSERT INTO "user" VALUES (${ownerId}) ON CONFLICT DO NOTHING`;
  await sql()`INSERT INTO subscription VALUES (${ownerId},'unlimited','active',NULL)`;
  await sql()`INSERT INTO watchlist (id,user_id) VALUES (${watchlistId},${ownerId})`;
  const state = await putAiFilterConfiguration({ ownerId, watchlistId, query: "AI development", candidateLanguages: ["en"] });
  return { ownerId, watchlistId, queryVersionId: state.queryVersionId, leaseOwner: randomUUID(), demandTargetOffset: 10_000, kind: "freshness" as const };
}

describe.skipIf(!process.env.AI_FILTER_TEST_DATABASE_URL)("freshness execution with PostgreSQL", () => {
  beforeEach(async () => {
    await sql().unsafe('TRUNCATE "user", subscription, watchlist_company, user_preferences CASCADE');
    mocks.postings = [];
    mocks.classify.mockReset().mockImplementation(async ({ jobs }) => ({
      model: JEV_MODEL, promptVersion: AI_FILTER_PROMPT_VERSION,
      decisions: jobs.map((job: { payload: { candidateId: string } }) => ({ candidateId: job.payload.candidateId, decision: "accepted" })),
      usage: { inputTokens: 100, outputTokens: 0 }, attempts: 1, ambiguousFailedAttempts: 0, latencyMs: 1,
    }));
  });

  it("admits only two live freshness segments across owners and skips further scope preparation", async () => {
    const first = await fixture("first-owner");
    const second = await fixture("second-owner");
    const third = await fixture("third-owner");
    for (const input of [first, second]) {
      await sql()`INSERT INTO ai_filter_segment
        (kind,watchlist_id,owner_id,query_version_id,status,lease_owner,lease_expires_at,window_start,window_end,idempotency_key)
        VALUES ('freshness',${input.watchlistId},${input.ownerId},${input.queryVersionId},'processing',${randomUUID()},now()+interval '5 minutes','2000-01-01',now(),${randomUUID()})`;
    }
    expect(await runAiFilterCatchupStep(third)).toMatchObject({ status: "busy", segmentId: null });
    expect(await claimAiFilterRefreshTargets(new Date())).toEqual([]);
    expect(mocks.classify).not.toHaveBeenCalled();
    // This search-specific ceiling must not replace the existing historical limits.
    expect(await runAiFilterCatchupStep({ ...third, kind: "historical" })).toMatchObject({ status: "caught_up" });
    await sql()`UPDATE ai_filter_segment SET lease_expires_at=now()-interval '1 second' WHERE owner_id=${first.ownerId}`;
    expect((await claimAiFilterRefreshTargets(new Date())).length).toBeGreaterThan(0);
    expect(await runAiFilterCatchupStep(third)).toMatchObject({ status: "caught_up" });
  });

  it("evaluates new and late-indexed postings ahead of a frozen 2200-job historical cursor", async () => {
    const input = await fixture();
    const historyId = randomUUID();
    await sql()`INSERT INTO ai_filter_segment (id,watchlist_id,owner_id,query_version_id,status,selection_offset,scanned_count,cursor,window_start,window_end,idempotency_key)
      VALUES (${historyId},${input.watchlistId},${input.ownerId},${input.queryVersionId},'completed',2150,50,50,'2000-01-01','2026-09-30','old-history')`;
    const fresh = { id: randomUUID(), firstSeenAt: new Date(Date.now() - 5000).toISOString() };
    const late = { id: randomUUID(), firstSeenAt: "2026-09-20T12:00:00Z" };
    mocks.postings = [fresh, late];
    expect((await runAiFilterCatchupStep(input)).status).toBe("continue");
    expect((await runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() })).status).toBe("caught_up");
    const rows = await sql()`SELECT candidate_id FROM ai_filter_decision ORDER BY posting_first_seen_at DESC`;
    expect(rows.map(row => row.candidate_id)).toEqual([fresh.id, late.id]);
    expect((await sql()`SELECT selection_offset,window_end FROM ai_filter_segment WHERE id=${historyId}`)[0]!.selection_offset).toBe(2150);
    expect(mocks.classify).toHaveBeenCalledOnce();
    expect(await getAiFilterOwnerState(input)).toMatchObject({ status: "caught_up", counts: { accepted: 2 } });
    expect((await listAiFilterDecisions({ ...input, bucket: "accepted" })).decisions).toHaveLength(2);
    const newArrival = { id: randomUUID(), firstSeenAt: new Date(Date.now() - 500).toISOString() };
    mocks.postings.unshift(newArrival);
    expect((await runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() })).status).toBe("continue");
    expect(mocks.classify.mock.calls[1]![0].jobs.map((job: { payload: { candidateId: string } }) => job.payload.candidateId)).toEqual([newArrival.id]);
  });

  it("keeps one active segment per lane and excludes duplicate workers", async () => {
    const input = await fixture();
    await sql()`INSERT INTO ai_filter_segment (watchlist_id,owner_id,query_version_id,status,window_start,window_end,idempotency_key,lease_owner,lease_expires_at)
      VALUES (${input.watchlistId},${input.ownerId},${input.queryVersionId},'processing','2000-01-01',now(),'active-history','history',now()+interval '5 minutes')`;
    mocks.postings = [{ id: randomUUID(), firstSeenAt: new Date(Date.now() - 1000).toISOString() }];
    const results = await Promise.all([
      runAiFilterCatchupStep(input), runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() }),
    ]);
    expect(results.map(result => result.status).sort()).toEqual(["busy", "continue"]);
    expect(mocks.classify).toHaveBeenCalledOnce();
  });

  it("renews expired decisions and cache entries once, retaining owner overrides", async () => {
    const input = await fixture();
    const post = { id: randomUUID(), firstSeenAt: "2026-09-10T12:00:00Z" };
    mocks.postings = [post];
    await runAiFilterCatchupStep(input);
    await sql()`UPDATE ai_filter_decision SET decided_at=now()-interval '31 days',expires_at=now()-interval '1 day',user_override='rejected'`;
    await sql()`UPDATE ai_filter_global_cache SET expires_at=now()-interval '1 day'`;
    expect((await runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() })).status).toBe("continue");
    expect((await runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() })).status).toBe("caught_up");
    const decisions = await sql()`SELECT candidate_id,user_override,expires_at > now() AS current FROM ai_filter_decision`;
    expect(decisions).toHaveLength(1);
    expect(decisions[0]).toMatchObject({ candidate_id: post.id, user_override: "rejected", current: true });
    expect(mocks.classify).toHaveBeenCalledTimes(2);
  });

  it("renews an expired watchlist decision from a still-valid global cache without paid work", async () => {
    const input = await fixture();
    mocks.postings = [{ id: randomUUID(), firstSeenAt: "2026-09-10T12:00:00Z" }];
    await runAiFilterCatchupStep(input);
    await sql()`UPDATE ai_filter_decision SET decided_at=now()-interval '31 days',expires_at=now()-interval '1 day'`;
    await runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() });
    expect((await sql()`SELECT expires_at > now() AS current FROM ai_filter_decision`)[0]!.current).toBe(true);
    expect(mocks.classify).toHaveBeenCalledOnce();
  });

  it("does not regress freshness coverage when old historical work finishes later", async () => {
    const input = await fixture();
    expect((await runAiFilterCatchupStep(input)).status).toBe("caught_up");
    const [before] = await sql()`SELECT last_caught_up_at FROM ai_filter_configuration`;
    // Resume an empty historical window after the freshness proof committed.
    await sql()`INSERT INTO ai_filter_segment (watchlist_id,owner_id,query_version_id,status,selection_offset,scanned_count,window_start,window_end,idempotency_key)
      VALUES (${input.watchlistId},${input.ownerId},${input.queryVersionId},'completed',100,50,'2000-01-01','2026-09-30','old-history')`;
    await runAiFilterCatchupStep({ ...input, kind: "historical", leaseOwner: randomUUID() });
    const [after] = await sql()`SELECT last_caught_up_at FROM ai_filter_configuration`;
    expect(after!.last_caught_up_at).toEqual(before!.last_caught_up_at);
    expect(await getAiFilterOwnerState(input)).toMatchObject({ status: "caught_up" });
  });

  it("does not run an obsolete scheduled revision or a disabled prompt", async () => {
    const input = await fixture();
    expect((await runAiFilterCatchupStep({ ...input, queryVersionId: randomUUID() })).status).toBe("disabled");
    await sql()`UPDATE ai_filter_configuration SET status='disabled',disabled_at=now()`;
    expect((await runAiFilterCatchupStep(input)).status).toBe("disabled");
    await expect(putAiFilterConfiguration({ ...input, query: "AI development", candidateLanguages: ["en"], expectedEnabledQueryVersionId: input.queryVersionId })).rejects.toThrow("not found");
    expect(mocks.classify).not.toHaveBeenCalled();
  });

  it("preserves caught-up coverage across disjoint historical windows", async () => {
    const input = await fixture();
    await sql()`INSERT INTO ai_filter_segment (watchlist_id,owner_id,query_version_id,status,window_start,window_end,idempotency_key)
      VALUES (${input.watchlistId},${input.ownerId},${input.queryVersionId},'caught_up','2000-01-01','2026-09-30','foundation')`;
    await sql()`UPDATE ai_filter_configuration SET last_caught_up_at='2026-09-30',last_sweep_at=now()`;
    expect((await runAiFilterCatchupStep({ ...input, kind: "historical" })).status).toBe("caught_up");
    expect(await getAiFilterOwnerState(input)).toMatchObject({ status: "caught_up" });
  });

  it("claims duplicate cron ticks atomically and retries lost dispatches on the next minute", async () => {
    await fixture();
    const now = new Date();
    const ticks = await Promise.all([claimAiFilterRefreshTargets(now), claimAiFilterRefreshTargets(now)]);
    expect(ticks.flat()).toHaveLength(1);
    expect(await claimAiFilterRefreshTargets(now)).toHaveLength(0);
    expect(await claimAiFilterRefreshTargets(new Date(now.getTime() + 60_000))).toHaveLength(1);
  });

  it("excludes disabled and unsubscribed owners from scheduled claims", async () => {
    await fixture();
    await sql()`UPDATE subscription SET status='expired'`;
    expect(await claimAiFilterRefreshTargets(new Date())).toHaveLength(0);
    await sql()`UPDATE subscription SET status='active'`;
    await sql()`UPDATE ai_filter_configuration SET status='disabled',disabled_at=now()`;
    expect(await claimAiFilterRefreshTargets(new Date())).toHaveLength(0);
  });
});
