import { writeFile } from "node:fs/promises";
import postgres from "postgres";
import dotenv from "dotenv";
dotenv.config({path: process.env.NARROWED_PRODUCTION_ENV_FILE ?? ".vercel/.env.production.local", quiet:true});
import { provisionNarrowedWorkerRole, verifyNarrowedWorkerReadAccess, NARROWED_WORKER_ROLE } from "./narrowed-worker-role";
import { parseStableCandidateOrderReadinessReceipt } from "../src/lib/search/stable-candidate-order-readiness";
import { applyNarrowedDeploymentPolicy } from "../src/lib/ai-filter/deployment-policy";
import { createHash } from "node:crypto";

async function main() {
  const policy = applyNarrowedDeploymentPolicy(process.env.NARROWED_RUNTIME_POLICY_JSON);
  const output = process.argv[2];
  const ownerUrl = process.env.DATABASE_URL_UNPOOLED;
  const password = process.env.NARROWED_WORKER_DATABASE_PASSWORD;
  if (!ownerUrl || !password || !output) throw new Error("Missing protected worker provisioning inputs");
  const url = new URL(ownerUrl);
  if (url.port === "6543") throw new Error("The notification worker needs a direct/session connection");
  const config: Record<string,string> = {};
  for (const key of ["TYPESENSE_HOST", "TYPESENSE_PORT", "TYPESENSE_PROTOCOL", "TYPESENSE_SEARCH_KEY",
    "AI_FILTER_ENABLED", "AI_FILTER_JEV_1_13_0_ENABLED", "R2_DOMAIN_URL", "TYPESAFE_AI_TOKEN", "AI_FILTER_CACHE_HMAC_SECRET", "TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT"]) {
    const value = process.env[key];
    if (!value?.trim()) throw new Error("A protected worker setting is missing");
    config[key] = value;
  }
  if (Buffer.byteLength(config.AI_FILTER_CACHE_HMAC_SECRET!, "utf8") < 32 ||
    !parseStableCandidateOrderReadinessReceipt(config.TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT)) throw new Error("Invalid worker configuration");
  if (policy && (createHash("sha256").update(config.AI_FILTER_CACHE_HMAC_SECRET!).digest("hex") !== policy.hmacSha256 ||
    createHash("sha256").update(config.TYPESAFE_AI_TOKEN!).digest("hex") !== policy.typesafeSha256)) throw new Error("Worker credentials differ from the recovered production runtime");
  for (const key of ["AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS", "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS",
    "AI_FILTER_MAX_SEGMENTS_PER_USER", "AI_FILTER_MAX_SEGMENTS_PROJECT", "STRIPE_ENVIRONMENT", "PADDLE_ENVIRONMENT"]) {
    if (process.env[key]) config[key] = process.env[key]!;
  }
  const sessionTenant = url.hostname.endsWith(".pooler.supabase.com") && url.username.includes(".")
    ? url.username.slice(url.username.indexOf(".")) : "";
  url.username = NARROWED_WORKER_ROLE + sessionTenant;
  url.password = password;
  config.DATABASE_URL = url.toString();
  const admin = postgres(ownerUrl, { max: 1, prepare: false, onnotice: () => {} });
  const worker = postgres(config.DATABASE_URL, { max: 1, prepare: false, onnotice: () => {}, connection: { default_transaction_read_only: true } });
  try {
    await provisionNarrowedWorkerRole(admin, password);
    await verifyNarrowedWorkerReadAccess(admin, worker);
  } finally { await Promise.all([admin.end({ timeout: 5 }), worker.end({ timeout: 5 })]); }
  // Compact JSON remains one env-file line; embedded newlines are JSON-escaped.
  await writeFile(output, `JOBSEEK_NARROWED_CONFIG_JSON=${JSON.stringify(config)}\n`, { mode: 0o600, flag: "wx" });
  console.info(JSON.stringify({ event: "narrowed_worker_configuration_prepared" }));
}
void main().catch(() => { console.error("Narrowed worker configuration failed"); process.exitCode = 1; });
