// @vitest-environment node
import { readFile } from "node:fs/promises";
import { narrowedWorkerReadinessKey } from "./host-contract";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";
import type { Sql } from "postgres";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { withTestEnv } from "@/test-utils/env";
import { provisionNarrowedWorkerRole } from "../../../scripts/narrowed-worker-role";

const mocks = vi.hoisted(() => ({
  client: null as Sql | null,
  workerDb: null as object | null,
  postings: [] as Array<{ id: string; firstSeenAt: string }>,
  classify: vi.fn(),
  statements: [] as string[],
}));
vi.mock("server-only", () => ({}));
vi.mock("@/db", async () => {
  if (!process.env.AI_FILTER_TEST_DATABASE_URL) return { db: {} };
  const uri = new URL(process.env.AI_FILTER_TEST_DATABASE_URL);
  if (
    !["localhost", "127.0.0.1"].includes(uri.hostname) ||
    !uri.pathname.endsWith("_fixture")
  ) {
    throw new Error(
      "AI filter tests require an explicit localhost *_fixture database",
    );
  }
  const { default: postgres } = await import("postgres");
  const { drizzle } = await import("drizzle-orm/postgres-js");
  const schema = await import("@/db/schema");
  mocks.client = postgres(uri.toString(), {
    max: 5,
    prepare: false,
    connection: { TimeZone: "UTC" },
    onnotice: () => {},
    debug: (_connection, query) => {
      mocks.statements.push(query);
    },
  });
  const base = drizzle(mocks.client, { schema });
  return { db: new Proxy(base, {get(_target,property) {
    const target=mocks.workerDb ?? base;
    const value=Reflect.get(target,property,target);
    return typeof value === "function" ? value.bind(target) : value;
  }}) };
});
vi.mock("@/lib/services/search", () => ({
  getPostingDetail: async () => null,
}));
vi.mock("@/lib/services/watchlist-matcher", () => ({
  compileWatchlistMatcherSources: async (sources: unknown[]) =>
    sources.map(() => ({
      candidateFilters: { anyCompany: true, companyIds: [] },
    })),
  readWatchlistCandidatesByIds: async (ids: string[]) =>
    mocks.postings.filter((post) => ids.includes(post.id)),
  readWatchlistCandidates: async (input: {
    excludePostingIds?: string[];
    window?: { windowEnd: Date };
    limit: number;
    offset: number;
  }) => {
    const posts = mocks.postings
      .filter(
        (post) =>
          !input.excludePostingIds?.includes(post.id) &&
          (!input.window ||
            Date.parse(post.firstSeenAt) < input.window.windowEnd.getTime()),
      )
      .sort((a, b) => Date.parse(b.firstSeenAt) - Date.parse(a.firstSeenAt));
    return {
      total: posts.length,
      postings: posts
        .slice(input.offset, input.offset + input.limit)
        .map((post) => ({
          ...post,
          title: "AI Engineer",
          company: {
            id: "company",
            name: "Fixture",
            slug: "fixture",
            icon: null,
          },
          sourceUrl: "https://example.test/job",
          locationNames: [],
          isActive: true,
          classifierMetadata: {
            locations: [{ name: "Zurich", type: "onsite" }],
            employmentType: "full_time",
            seniorityName: null,
            experienceMin: null,
            experienceMax: null,
            technologies: [],
            salaryMin: null,
            salaryMax: null,
            salaryCurrency: null,
            salaryPeriod: null,
            descriptionLocale: "en",
          },
        })),
    };
  },
}));
vi.mock("./mining-policy", () => ({
  assertAiFilterMiningAllowed: async () => {},
}));
vi.mock("./jev-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./jev-client")>();
  return {
    ...actual,
    JevClient: class {
      classify = mocks.classify;
    },
  };
});
vi.mock("./workflow-trigger", () => ({ startAiFilterCatchup: vi.fn() }));

import {
  enqueueAiFilterWork,
  claimAiFilterWorker,
  claimAiFilterWork,
  finishAiFilterWork,
  releaseAiFilterWorker,
} from "./work-queue";
import { runAiFilterCatchupStep } from "./catchup-service";
import { runAiFilterRefreshSweep } from "./refresh-service";
import {
  getAiFilterOwnerState,
  putAiFilterConfiguration,
} from "./configuration-service";
import { AI_FILTER_PROMPT_VERSION, JEV_MODEL } from "./policy";

withTestEnv({
  VERCEL_ENV: "production",
  JOBSEEK_NARROWED_WORKER: "1",
  AI_FILTER_ENABLED: "true",
  AI_FILTER_JEV_1_13_0_ENABLED: "true",
  TYPESAFE_AI_TOKEN: "fixture",
  AI_FILTER_CACHE_HMAC_SECRET: "x".repeat(32),
  PADDLE_ENVIRONMENT: undefined,
  STRIPE_ENVIRONMENT: undefined,
});
const sql = () => mocks.client!;
beforeAll(async () => {
  if (!mocks.client) return;
  await sql()
    .unsafe(`DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;
    CREATE TABLE "user" (id text PRIMARY KEY);
    CREATE TABLE watchlist (id uuid PRIMARY KEY, user_id text REFERENCES "user"(id), title text DEFAULT 'AI', filters jsonb DEFAULT '{"anyCompany":true}');
    CREATE TABLE watchlist_company (watchlist_id uuid, company_id uuid);
    CREATE TABLE user_preferences (user_id text, locale text DEFAULT 'en', job_languages text[] DEFAULT '{}');
    CREATE TABLE subscription (user_id text, plan text, status text, ends_at timestamptz);`);
  for (const file of [
    "0092_jev_ai_filter_foundation.sql",
    "0093_ai_filter_historical_horizon.sql",
    "0094_ai_filter_candidate_languages.sql",
    "0103_ai_filter_freshness.sql",
    "0104_ai_filter_shared_worker.sql",
    "0105_ai_filter_interactive_demand.sql",
  ]) {
    for (const statement of (await readFile(`drizzle/${file}`, "utf8"))
      .split("--> statement-breakpoint")
      .filter((s) => s.trim())) {
      await sql().unsafe(statement);
    }
  }
});
afterAll(async () => {
  await mocks.client?.end();
});

async function fixture(ownerId = "owner") {
  const watchlistId = randomUUID();
  await sql()`INSERT INTO "user" VALUES (${ownerId}) ON CONFLICT DO NOTHING`;
  await sql()`INSERT INTO subscription VALUES (${ownerId},'unlimited','active',NULL)`;
  await sql()`INSERT INTO watchlist (id,user_id) VALUES (${watchlistId},${ownerId})`;
  const state = await putAiFilterConfiguration({
    ownerId,
    watchlistId,
    query: "AI development",
    candidateLanguages: ["en"],
  });
  return {
    ownerId,
    watchlistId,
    queryVersionId: state.queryVersionId,
    demandTargetOffset: 10000,
    kind: "freshness" as const,
  };
}
async function unlockTick() {
  await sql()`UPDATE ai_filter_worker SET next_tick_at=now()-interval '1 second'`;
  await sql()`UPDATE ai_filter_work SET available_at=now()-interval '1 second' WHERE status='queued'`;
}
async function tick() {
  await unlockTick();
  return runAiFilterRefreshSweep();
}
const posting = (date = "2026-09-20T12:00:00Z") => ({
  id: randomUUID(),
  firstSeenAt: date,
});

describe.skipIf(!process.env.AI_FILTER_TEST_DATABASE_URL)(
  "shared worker transactions and recovery",
  () => {
    beforeEach(async () => {
      await sql().unsafe(
        'TRUNCATE "user",subscription,watchlist_company,user_preferences CASCADE',
      );
      await sql()`UPDATE ai_filter_worker SET lease_owner=NULL,lease_expires_at=NULL,next_tick_at=now(),blocked_until=NULL,reason=NULL,failures=0,next_lane='historical',interactive_streak=0`;
      mocks.postings = [];
      mocks.classify.mockReset().mockImplementation(async ({ jobs }) => ({
        model: JEV_MODEL,
        promptVersion: AI_FILTER_PROMPT_VERSION,
        decisions: jobs.map((job: { payload: { candidateId: string } }) => ({
          candidateId: job.payload.candidateId,
          decision: "accepted",
        })),
        usage: { inputTokens: 100, outputTokens: 0 },
        attempts: 1,
        ambiguousFailedAttempts: 0,
        latencyMs: 1,
      }));
    });
    it("notifies the standalone worker only after demand is durable", async () => {
      const input = await fixture();
      let notified!: () => void;
      const notification = new Promise<void>(resolve => { notified = resolve; });
      const subscription = await sql().listen("jobseek_narrowed_work", payload => {
        expect(payload).toBe("");
        notified();
      });
      try {
        await enqueueAiFilterWork({...input, kind:"historical", acceptedTarget:20});
        await Promise.race([notification, new Promise((_,reject) => setTimeout(() => reject(new Error("Missing worker notification")),2000))]);
        const [work] = await sql()`SELECT status FROM ai_filter_work WHERE query_version_id=${input.queryVersionId} AND kind='historical'`;
        expect(work?.status).toBe("queued");
      } finally { await subscription.unlisten(); }
    });
    it("runs the shared classifier with the restricted host role and cannot read authentication or mutate watchlists", async () => {
      const input = await fixture();
      mocks.postings = [posting()];
      await enqueueAiFilterWork({...input,kind:"historical",demandTargetOffset:500,acceptedTarget:20});
      const secured = await sql()`SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND (c.relname LIKE 'ai_filter_%' OR c.relname IN ('watchlist','watchlist_company','user_preferences','subscription','currency_rate'))`;
      for (const table of secured) await sql().unsafe(`ALTER TABLE public.${table.relname} ENABLE ROW LEVEL SECURITY`);
      await provisionNarrowedWorkerRole(sql(),"fixture".repeat(8));
      const { verifyNarrowedWorkerReadAccess } = await import("../../../scripts/narrowed-worker-role");
      const [permissions] = await sql()`SELECT
        has_table_privilege('jobseek_narrowed_worker','watchlist','SELECT') AS can_read,
        has_table_privilege('jobseek_narrowed_worker','watchlist','UPDATE') AS can_update,
        has_table_privilege('jobseek_narrowed_worker','"user"','SELECT') AS can_read_auth`;
      expect(permissions).toMatchObject({can_read:true,can_update:false,can_read_auth:false});
      const [{ default: postgres },{ drizzle },schema] = await Promise.all([import("postgres"),import("drizzle-orm/postgres-js"),import("@/db/schema")]);
      const uri = new URL(process.env.AI_FILTER_TEST_DATABASE_URL!);
      uri.username="jobseek_narrowed_worker";uri.password="fixture".repeat(8);
      const rawClient=postgres(uri.toString(),{max:2,prepare:false,onnotice:()=>{}});
      const client=rawClient;
      mocks.workerDb=drizzle(client,{schema});
      try {
        await verifyNarrowedWorkerReadAccess(sql(),client);
        const [policy] = await sql()`SELECT count(*)::int AS n FROM pg_policies WHERE policyname LIKE 'jobseek_narrowed_worker_%'
          AND (roles<>ARRAY['jobseek_narrowed_worker']::name[] OR cmd NOT IN ('SELECT','INSERT','UPDATE'))`;
        expect(policy?.n).toBe(0);
        await sql().unsafe("DROP POLICY jobseek_narrowed_worker_select ON ai_filter_configuration");
        await expect(verifyNarrowedWorkerReadAccess(sql(),client)).rejects.toThrow("Worker row visibility differs");
        await provisionNarrowedWorkerRole(sql(),"fixture".repeat(8));
        await expect(client`UPDATE watchlist SET title='forbidden'`).rejects.toThrow();
        await expect(client`SELECT * FROM "user"`).rejects.toThrow();
        const result=await tick();
        expect(result).toMatchObject({failed:0});
        expect(result.started).toBeGreaterThan(0);
        expect(mocks.classify).toHaveBeenCalled();
        const [decision]=await client`SELECT count(*)::int AS n FROM ai_filter_decision WHERE watchlist_id=${input.watchlistId}`;
        expect(decision?.n).toBe(1);
        // The actual secretless build boots with only the restricted host role.
        const receipt=Buffer.from(JSON.stringify({schemaVersion:"typesense-stable-candidate-order-readiness-v1",
          keyVersion:"uuid-int64-active-v1",partitions:256,unresolved:0,authoritativeCount:1,
          reconciliationRunId:"ff96b7d8-db7e-43a2-aafd-e4007fa68fa9",benchmarkSha256:"a".repeat(64),
          completedAt:"2026-10-16T00:00:00Z"})).toString("base64url");
        const config={DATABASE_URL:uri.toString(),AI_FILTER_ENABLED:"true",AI_FILTER_JEV_1_13_0_ENABLED:"true",
          TYPESENSE_HOST:"127.0.0.1",TYPESENSE_PORT:"1",TYPESENSE_PROTOCOL:"http",TYPESENSE_SEARCH_KEY:"fixture",
          R2_DOMAIN_URL:"https://fixture.invalid",TYPESAFE_AI_TOKEN:"fixture",AI_FILTER_CACHE_HMAC_SECRET:"x".repeat(32),
          TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT:receipt};
        const output=execFileSync(process.execPath,["../../deploy/ai-filter-refresh/bundle/worker.mjs","--check"],{
          env:{...process.env,NODE_ENV:"production",JOBSEEK_NARROWED_CONFIG_JSON:JSON.stringify(config)},encoding:"utf8"});
        expect(output).toContain('"status":"ready"');
        expect(output).not.toContain(uri.toString());
        const verification = (extra: Partial<NodeJS.ProcessEnv> = {}) => execFileSync(process.execPath,["--import","tsx","scripts/verify-narrowed-worker-ready.ts"],{
          env:{...process.env,DATABASE_URL_UNPOOLED:process.env.AI_FILTER_TEST_DATABASE_URL,
            AI_FILTER_ENABLED:"true",AI_FILTER_JEV_1_13_0_ENABLED:"true",GITHUB_OUTPUT:undefined,...extra},encoding:"utf8",stdio:"pipe"});
        expect(verification).toThrow();
        expect(() => verification({ AI_FILTER_ENABLED: "" })).toThrow();
        expect(() => verification({ AI_FILTER_JEV_1_13_0_ENABLED: undefined })).toThrow();
        expect(verification({ AI_FILTER_ENABLED: "false" })).toContain('"status":"disabled"');
        const hash=createHash("sha256").update(await readFile("../../deploy/ai-filter-refresh/bundle/worker.mjs")).digest("hex");
        const held=await client.reserve();
        try {
          await held`SELECT pg_advisory_lock(hashtextextended(${narrowedWorkerReadinessKey(hash)},0))`;
          expect(verification()).toContain('"status":"ready"');
          const recoveredPolicy = { version: 1, hmacSha256: createHash("sha256").update(config.AI_FILTER_CACHE_HMAC_SECRET).digest("hex"),
            typesafeSha256: createHash("sha256").update(config.TYPESAFE_AI_TOKEN).digest("hex"), values: {
              ...Object.fromEntries(["AI_FILTER_ENABLED", "AI_FILTER_JEV_1_13_0_ENABLED", "AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS",
                "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS", "AI_FILTER_MAX_SEGMENTS_PER_USER", "AI_FILTER_MAX_SEGMENTS_PROJECT",
                "STRIPE_ENVIRONMENT", "PADDLE_ENVIRONMENT"].map(name => [name, process.env[name] || null])),
              TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: receipt,
            } };
          expect(verification({ AI_FILTER_ENABLED: "", AI_FILTER_JEV_1_13_0_ENABLED: "", NARROWED_RUNTIME_POLICY_JSON: JSON.stringify(recoveredPolicy) })).toContain('"status":"ready"');
          expect(()=>verification({AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS:"1"})).toThrow();
        } finally { await held`SELECT pg_advisory_unlock_all()`; held.release(); }
        expect(verification).toThrow();
        await client`UPDATE ai_filter_work SET status='idle'`;
        await client`UPDATE ai_filter_configuration SET refresh_requested_at=now()`;
        const processUri = new URL(uri);
        processUri.searchParams.set("application_name","narrowed-process-fixture");
        const native=spawn(process.execPath,["../../deploy/ai-filter-refresh/bundle/worker.mjs"],{
          env:{...process.env,NODE_ENV:"production",VERCEL:undefined,
            JOBSEEK_NARROWED_CONFIG_JSON:JSON.stringify({...config,DATABASE_URL:processUri.toString()})},stdio:["ignore","pipe","pipe"]});
        let nativeOutput="";
        native.stdout.on("data",chunk=>{ nativeOutput+=String(chunk); });
        native.stderr.on("data",chunk=>{ nativeOutput+=String(chunk); });
        const exit = new Promise(resolve=>native.once("exit",resolve));
        const waitFor = async (check:()=>Promise<boolean>) => {
          const end=Date.now()+3000;
          while(Date.now()<end) { if(await check()) return; await new Promise(resolve=>setTimeout(resolve,25)); }
          throw new Error("Standalone worker did not reach the fixture checkpoint");
        };
        try {
          await waitFor(async()=>nativeOutput.includes('"event":"narrowed_worker_ready"') && nativeOutput.includes('"status":"completed"'));
          const [before] = await client`SELECT max(query_start)::text AS at FROM pg_stat_activity WHERE application_name='narrowed-process-fixture'`;
          expect(before?.at).toBeTruthy();
          await client`SELECT pg_notify('jobseek_narrowed_work','')`;
          await waitFor(async()=>{
            const [row]=await client`SELECT EXISTS(SELECT 1 FROM pg_stat_activity
              WHERE application_name='narrowed-process-fixture' AND query_start>${before.at}::timestamptz) AS woke`;
            return row?.woke===true;
          });
          expect(nativeOutput).not.toContain('"status":"failed"');
          expect(verification()).toContain('"status":"ready"');
        } finally {
          native.kill("SIGTERM");
          const kill=setTimeout(()=>native.kill("SIGKILL"),3000);
          await exit;clearTimeout(kill);
        }
        expect(verification).toThrow();
      } finally { mocks.workerDb=null; await client.end(); }
    },20_000);
    it("coalesces tabs and extensions before dispatch and retains extensions arriving during processing", async () => {
      const input = await fixture();
      await Promise.all(
        Array.from({ length: 20 }, (_, index) =>
          enqueueAiFilterWork({
            ...input,
            kind: "historical",
            demandTargetOffset: 500 + index,
          }),
        ),
      );
      const [row] =
        await sql()`SELECT * FROM ai_filter_work WHERE kind='historical'`;
      expect(Number(row!.target_offset)).toBe(519);
      expect(Number(row!.generation)).toBe(20);
      const owner = await claimAiFilterWorker();
      expect(owner).toBeTruthy();
      const target = await claimAiFilterWork(owner!);
      expect(target!.kind).toBe("historical");
      await enqueueAiFilterWork({
        ...input,
        kind: "historical",
        demandTargetOffset: 1000,
      });
      await finishAiFilterWork(target!, "demand_satisfied");
      const [after] =
        await sql()`SELECT * FROM ai_filter_work WHERE kind='historical'`;
      expect(after!.status).toBe("queued");
      expect(after!.target_offset).toBe(1000);
    });
    it("permits one worker at a fixed cadence even with concurrent host/manual requests", async () => {
      await fixture();
      const owners = await Promise.all(
        Array.from({ length: 10 }, () => claimAiFilterWorker()),
      );
      expect(owners.filter(Boolean)).toHaveLength(1);
      await releaseAiFilterWorker(owners.find(Boolean)!, false);
      expect(await claimAiFilterWorker()).toBeNull();
    });
    it.each([1, 10, 100])(
      "leaves the singleton untouched with %i scopes when no work or repair is due",
      async (count) => {
        for (let index = 0; index < count; index++)
          await fixture(`recent-${index}`);
        await sql()`UPDATE ai_filter_work SET status='idle'`;
        await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=now()`;
        const [before] = await sql()`SELECT *,xmin::text AS version FROM ai_filter_worker`;
        mocks.statements = [];
        expect(await runAiFilterRefreshSweep()).toMatchObject({ claimed: 0, started: 0, failed: 0 });
        expect(mocks.statements).toHaveLength(1);
        expect((await sql()`SELECT *,xmin::text AS version FROM ai_filter_worker`)[0]).toEqual(before);
        expect(mocks.classify).not.toHaveBeenCalled();
        // Skipping an empty tick must not delay newly committed foreground demand.
        const [scope] = await sql()`SELECT owner_id,watchlist_id FROM ai_filter_configuration`;
        await enqueueAiFilterWork({
          ownerId: scope.owner_id,
          watchlistId: scope.watchlist_id,
          kind: "historical",
          demandTargetOffset: 20,
        });
        expect(await claimAiFilterWorker()).toBeTruthy();
      },
    );
    it("admits a due repair even when every demand row is idle", async () => {
      await fixture();
      await sql()`UPDATE ai_filter_work SET status='idle'`;
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=now()-interval '6 minutes'`;
      expect(await claimAiFilterWorker()).toBeTruthy();
    });
    it("admits abandoned work only after its work lease expires", async () => {
      await fixture();
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=now()`;
      await sql()`UPDATE ai_filter_work SET status='processing',lease_owner='abandoned',lease_expires_at=now()+interval '1 minute'`;
      expect(await claimAiFilterWorker()).toBeNull();
      await sql()`UPDATE ai_filter_work SET lease_expires_at=now()-interval '1 second'`;
      expect(await claimAiFilterWorker()).toBeTruthy();
    });
    it("honors queued availability and paused retry backoff without spending idle leases", async () => {
      await fixture();
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=now()`;
      await sql()`UPDATE ai_filter_work SET available_at=now()+interval '5 minutes'`;
      expect(await claimAiFilterWorker()).toBeNull();
      await sql()`UPDATE ai_filter_work SET status='paused'`;
      expect(await claimAiFilterWorker()).toBeNull();
      await sql()`UPDATE ai_filter_work SET available_at=now()-interval '1 second'`;
      expect(await claimAiFilterWorker()).toBeTruthy();
    });
    it("ignores disabled, unentitled and obsolete-revision demand", async () => {
      const input = await fixture();
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=now()`;
      await sql()`UPDATE subscription SET status='cancelled'`;
      expect(await claimAiFilterWorker()).toBeNull();
      await sql()`UPDATE subscription SET status='active'`;
      await sql()`UPDATE ai_filter_configuration SET status='disabled',disabled_at=now()`;
      expect(await claimAiFilterWorker()).toBeNull();
      await putAiFilterConfiguration({ ...input, query: "new criteria", candidateLanguages: ["en"] });
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=now()`;
      await sql()`UPDATE ai_filter_work SET status=CASE WHEN query_version_id=${input.queryVersionId}::uuid THEN 'queued' ELSE 'idle' END`;
      expect(await claimAiFilterWorker()).toBeNull();
    });
    it.each([1, 10, 100])(
      "%i idle saved scopes execute zero segments and zero paid calls",
      async (count) => {
        for (let index = 0; index < count; index++)
          await fixture(`idle-${index}`);
        await sql()`UPDATE ai_filter_work SET status='idle'`;
        expect(await tick()).toMatchObject({ started: 0, failed: 0 });
        expect(
          (
            await sql()`SELECT count(*)::integer AS count FROM ai_filter_segment`
          )[0]!.count,
        ).toBe(0);
        expect(mocks.classify).not.toHaveBeenCalled();
      },
    );
    it("bounds a large backlog to five checkpoints, preserving materialized results across restart", async () => {
      const input = await fixture();
      mocks.postings = Array.from({ length: 100 }, () => posting());
      expect((await tick()).claimed).toBeLessThanOrEqual(5);
      expect(
        (
          await sql()`SELECT max(scanned_count) AS count FROM ai_filter_segment`
        )[0]!.count,
      ).toBeLessThanOrEqual(5);
      const count = (
        await sql()`SELECT count(*)::integer AS count FROM ai_filter_decision`
      )[0]!.count;
      expect(count).toBeGreaterThan(0);
      expect(count).toBeLessThanOrEqual(25);
      const state = await getAiFilterOwnerState(input);
      expect(state.coverage?.state).toBe("partial");
      await tick();
      expect((await getAiFilterOwnerState(input)).counts.total).toBeGreaterThan(
        count,
      );
    });
    it("rejects legacy deployment leases at the database even while saved criteria are enabled", async () => {
      const input = await fixture();
      await expect(
        runAiFilterCatchupStep({ ...input, leaseOwner: randomUUID() }),
      ).rejects.toMatchObject({
        cause: { message: "narrowed_shared_worker_required" },
      });
      expect(mocks.classify).not.toHaveBeenCalled();
    });
    it("recovers abandoned worker/work leases and does not duplicate classified jobs", async () => {
      const input = await fixture();
      mocks.postings = [posting()];
      const owner = await claimAiFilterWorker();
      const target = await claimAiFilterWork(owner!);
      expect(
        (await runAiFilterCatchupStep({ ...target!, candidateLimit: 5 }))
          .status,
      ).toBe("continue");
      await sql()`UPDATE ai_filter_worker SET lease_expires_at=now()-interval '1 second'`;
      await sql()`UPDATE ai_filter_work SET lease_expires_at=now()-interval '1 second'`;
      await tick();
      expect(mocks.classify).toHaveBeenCalledOnce();
      expect((await getAiFilterOwnerState(input)).counts.total).toBe(1);
    });
    it("rotates foreground and freshness lanes instead of letting a backlog monopolize capacity", async () => {
      const first = await fixture();
      const second = await fixture("second");
      await enqueueAiFilterWork({ ...second, kind: "historical" });
      const owner = await claimAiFilterWorker();
      const a = await claimAiFilterWork(owner!);
      expect(a!.kind).toBe("historical");
      await finishAiFilterWork(a!, "continue");
      const b = await claimAiFilterWork(owner!);
      expect(b!.kind).toBe("freshness");
      expect([first.watchlistId, second.watchlistId]).toContain(b!.watchlistId);
    });
    it("persists exponential backoff and a shared failure circuit across invocations", async () => {
      const input = await fixture();
      for (let failure = 0; failure < 3; failure++) {
        await unlockTick();
        const owner = await claimAiFilterWorker();
        await releaseAiFilterWorker(owner!, true);
      }
      await unlockTick();
      expect(await claimAiFilterWorker()).toBeNull();
      expect(await getAiFilterOwnerState(input)).toMatchObject({
        status: "paused_infrastructure",
        coverage: { state: "unreviewed" },
      });
    });
    it("admits a first-page wake immediately after an idle slice, while retaining the singleton fence", async () => {
      const input = await fixture();
      const owner = await claimAiFilterWorker();
      await releaseAiFilterWorker(owner!, false);
      await sql()`UPDATE ai_filter_worker SET next_tick_at=now()+interval '1 minute'`;
      await enqueueAiFilterWork({ ...input, kind: "historical", acceptedTarget: 20 });
      const awakened = await claimAiFilterWorker();
      expect(awakened).toBeTruthy();
      expect(await claimAiFilterWorker()).toBeNull();
      await releaseAiFilterWorker(awakened!, false);
      await sql()`UPDATE ai_filter_worker SET blocked_until=now()+interval '15 minutes'`;
      expect(await claimAiFilterWorker()).toBeNull();
    });
    it("serves first-page demand before pagination and reserves every fourth turn for background work", async () => {
      const first = await fixture();
      const more = await fixture("pagination-owner");
      await enqueueAiFilterWork({ ...more, kind: "historical", acceptedTarget: 40 });
      await enqueueAiFilterWork({ ...first, kind: "historical", acceptedTarget: 20 });
      const owner = await claimAiFilterWorker();
      for (let turn = 0; turn < 3; turn++) {
        const target = await claimAiFilterWork(owner!);
        expect(target).toMatchObject({ watchlistId: first.watchlistId, kind: "historical", acceptedTarget: 20 });
        await finishAiFilterWork(target!, "continue");
      }
      const background = await claimAiFilterWork(owner!);
      expect(background!.kind).toBe("freshness");
    });
    it("fills an accepted result page beyond a raw candidate runway and then yields", async () => {
      const input = await fixture();
      mocks.postings = Array.from({ length: 100 }, () => posting());
      await enqueueAiFilterWork({ ...input, kind: "historical", demandTargetOffset: 5, acceptedTarget: 20 });
      await tick();
      const state = await getAiFilterOwnerState(input);
      expect(state.counts.accepted).toBeGreaterThanOrEqual(20);
      expect(state.counts.total).toBeLessThan(100);
      const [work] = await sql()`SELECT status FROM ai_filter_work WHERE query_version_id=${input.queryVersionId} AND kind='historical'`;
      expect(work.status).toBe("idle");
    });
    it("rejects index growth beyond the candidate ceiling before paid classification", async () => {
      const input = await fixture();
      const owner = await claimAiFilterWorker();
      const target = await claimAiFilterWork(owner!);
      mocks.postings = Array.from({ length: 10_001 }, () => posting());
      expect(await runAiFilterCatchupStep({ ...target!, candidateLimit: 5 })).toMatchObject({ status: "paused_kill" });
      expect(mocks.classify).not.toHaveBeenCalled();
      expect((await getAiFilterOwnerState(input)).progress.stopReason).toBe("scope_too_broad");
    });
    it("reviews late-indexed old postings and expired decisions without an owner page request", async () => {
      const input = await fixture();
      mocks.postings = [posting()];
      await tick();
      await tick();
      const late = posting("2026-08-01T12:00:00Z");
      mocks.postings.push(late);
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=NULL`;
      await tick();
      await tick();
      expect((await getAiFilterOwnerState(input)).counts.total).toBe(2);
      await sql()`UPDATE ai_filter_decision SET decided_at=now()-interval '29 days',expires_at=now()-interval '1 second'`;
      await sql()`UPDATE ai_filter_global_cache SET expires_at=now()-interval '1 second' WHERE status='ready'`;
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=NULL`;
      await tick();
      await tick();
      expect(mocks.classify.mock.calls.length).toBeGreaterThanOrEqual(3);
    });
    it("publishes coverage without invalidating email plans for unchanged criteria", async () => {
      await fixture();
      const [before] = await sql()`SELECT updated_at FROM ai_filter_configuration`;
      mocks.postings = [posting()];
      await tick();
      await tick();
      const [after] = await sql()`SELECT updated_at,last_caught_up_at FROM ai_filter_configuration`;
      expect(after.updated_at).toEqual(before.updated_at);
      expect(after.last_caught_up_at).toBeTruthy();
      await sql()`UPDATE ai_filter_configuration SET refresh_requested_at=NULL`;
      await tick();
      const [repaired] = await sql()`SELECT updated_at FROM ai_filter_configuration`;
      expect(repaired.updated_at).toEqual(before.updated_at);
    });
    it("keeps zero reviewed distinct from completed evaluation with zero accepted", async () => {
      const input = await fixture();
      expect((await getAiFilterOwnerState(input)).coverage?.state).toBe(
        "unreviewed",
      );
      mocks.postings = [posting()];
      mocks.classify.mockImplementation(async ({ jobs }) => ({
        model: JEV_MODEL,
        promptVersion: AI_FILTER_PROMPT_VERSION,
        decisions: jobs.map((job: { payload: { candidateId: string } }) => ({
          candidateId: job.payload.candidateId,
          decision: "rejected",
        })),
        usage: { inputTokens: 100, outputTokens: 0 },
        attempts: 1,
        ambiguousFailedAttempts: 0,
        latencyMs: 1,
      }));
      await tick();
      await tick();
      expect(await getAiFilterOwnerState(input)).toMatchObject({
        status: "caught_up",
        counts: { accepted: 0, total: 1 },
        coverage: { state: "complete" },
      });
    });
    it("discards old revisions and disabled/unentitled scopes before paid work", async () => {
      const input = await fixture();
      mocks.postings = [posting()];
      await putAiFilterConfiguration({
        ...input,
        query: "different query",
        candidateLanguages: ["en"],
      });
      await sql()`UPDATE subscription SET status='cancelled'`;
      await tick();
      expect(mocks.classify).not.toHaveBeenCalled();
      await sql()`UPDATE subscription SET status='active'`;
      await sql()`UPDATE ai_filter_configuration SET status='disabled',disabled_at=now()`;
      await tick();
      expect(mocks.classify).not.toHaveBeenCalled();
    });
  },
);
