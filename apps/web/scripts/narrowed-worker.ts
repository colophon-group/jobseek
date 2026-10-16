import { writeFile, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import postgres from "postgres";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { runAiFilterRefreshSweep } from "@/lib/ai-filter/refresh-service";
import { inspectAiFilterRecovery } from "@/lib/ai-filter/recovery-service";
import { parseStableCandidateOrderReadinessReceipt } from "@/lib/search/stable-candidate-order-readiness";
import { runHostWorker, WorkerWakeup } from "@/lib/ai-filter/host-loop";
import { readAiFilterRuntimePolicy } from "@/lib/ai-filter/policy";
import { withMatchingDataReads } from "@/lib/search/matching-data";
import { narrowedWorkerPaused } from "@/lib/service-availability";
import { narrowedWorkerReadinessKey } from "@/lib/ai-filter/host-contract";

declare const NARROWED_RUNTIME_IMAGE: string;

const required = ["AI_FILTER_ENABLED", "AI_FILTER_JEV_1_13_0_ENABLED", "DATABASE_URL", "TYPESENSE_HOST", "TYPESENSE_PORT", "TYPESENSE_PROTOCOL",
  "TYPESENSE_SEARCH_KEY", "R2_DOMAIN_URL", "TYPESAFE_AI_TOKEN", "AI_FILTER_CACHE_HMAC_SECRET",
  "TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT"] as const;
const optional = ["AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS", "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS",
  "AI_FILTER_MAX_SEGMENTS_PER_USER", "AI_FILTER_MAX_SEGMENTS_PROJECT", "STRIPE_ENVIRONMENT", "PADDLE_ENVIRONMENT"] as const;

async function main() {
  // Docker reads the root-only systemd env-file; the unprivileged worker gets only this config.
  const raw = process.env.JOBSEEK_NARROWED_CONFIG_JSON;
  delete process.env.JOBSEEK_NARROWED_CONFIG_JSON;
  if (!raw || process.env.VERCEL) throw new Error("worker_configuration_unavailable");
  if (Buffer.byteLength(raw) > 32_768) throw new Error("worker_configuration_invalid");
  const config = JSON.parse(raw) as Record<string, unknown>;
  for (const key of [...required, ...optional]) {
    if (required.includes(key as typeof required[number]) &&
      (typeof config[key] !== "string" || !(config[key] as string).trim())) throw new Error("worker_configuration_invalid");
    if (config[key] !== undefined) {
      if (typeof config[key] !== "string") throw new Error("worker_configuration_invalid");
      process.env[key] = config[key] as string;
    }
  }
  if (Buffer.byteLength(process.env.AI_FILTER_CACHE_HMAC_SECRET!, "utf8") < 32 ||
    !parseStableCandidateOrderReadinessReceipt(process.env.TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT)) {
    throw new Error("worker_configuration_invalid");
  }
  if (process.env.NODE_ENV !== "production") throw new Error("worker_environment_invalid");
  // Preserve the shared production entitlement checks without executing on Vercel.
  process.env.VERCEL_ENV = "production";
  process.env.JOBSEEK_NARROWED_WORKER = "1";
  if (process.env.AI_FILTER_ENABLED !== "true" || process.env.AI_FILTER_JEV_1_13_0_ENABLED !== "true") throw new Error("worker_disabled");
  readAiFilterRuntimePolicy();
  if (narrowedWorkerPaused()) throw new Error("worker_paused");
  await db.execute(sql`SELECT interactive_until FROM ai_filter_work LIMIT 0`);
  const [fence] = await db.execute<{ enabled: boolean }>(sql`SELECT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname='ai_filter_shared_worker_fence' AND tgenabled='O') AS enabled`);
  if (!fence?.enabled) throw new Error("worker_database_contract_unavailable");
  if (process.argv[2] === "--check") {
    console.info(JSON.stringify({ event: "narrowed_worker_check", status: "ready" }));
    process.exit(0);
  }
  if (process.argv[2] === "--inspect" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(process.argv[3] ?? "")) {
    console.info(JSON.stringify({ event: "narrowed_worker_inspection", recovery: await inspectAiFilterRecovery(process.argv[3]!) }));
    process.exit(0);
  }
  if (process.argv.length !== 2) throw new Error("worker_arguments_invalid");
  const stop = new AbortController();
  process.once("SIGTERM", () => stop.abort());
  process.once("SIGINT", () => stop.abort());
  const wakeup = new WorkerWakeup();
  // Direct/unpooled Postgres connection. Payloads contain no user or query data.
  let lostBackend = false;
  const listener = postgres(process.env.DATABASE_URL!, { max: 1, idle_timeout: 0, max_lifetime: 0, prepare: false,
    onclose: () => { if (!stop.signal.aborted) { lostBackend = true; stop.abort(); } },
    connection: { application_name: "jobseek-narrowed-listener" }, onnotice: () => {} });
  try {
    await listener.listen("jobseek_narrowed_work", () => wakeup.wake());
    const bundleHash = createHash("sha256").update(await readFile(new URL(import.meta.url))).digest("hex");
    // A session lock proves the exact runtime bundle is alive before web promotion.
    await listener`SELECT pg_advisory_lock(hashtextextended(${narrowedWorkerReadinessKey(bundleHash)},0))`;
    await writeFile("/tmp/narrowed-worker-ready", "ready\n");
    console.info(JSON.stringify({ event: "narrowed_worker_ready", backend: "hetzner", runtimeImage: NARROWED_RUNTIME_IMAGE }));
    await runHostWorker({ signal: stop.signal, wakeup, sweep: () => withMatchingDataReads(runAiFilterRefreshSweep),
      report: (result) => console.info(JSON.stringify({ event: "narrowed_worker_slice", ...result })) });
  } finally {
    await listener.end({ timeout: 5 });
  }
  process.exit(lostBackend ? 1 : 0);
}

void main().catch(() => {
  // Credentials, query text and upstream bodies must never reach journald.
  console.error(JSON.stringify({ event: "narrowed_worker_failed" }));
  process.exit(1);
});
