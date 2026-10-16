import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import dotenv from "dotenv";
import postgres from "postgres";
import availability from "../src/config/service-availability.json";
import { narrowedWorkerReadinessKey } from "../src/lib/ai-filter/host-contract";
import { appendFileSync } from "node:fs";

dotenv.config({ path: resolve(process.cwd(), "../../.vercel/.env.production.local"), quiet: true });
async function main() {
  if (availability.narrowedWorkerPaused || process.env.AI_FILTER_ENABLED !== "true" ||
    process.env.AI_FILTER_JEV_1_13_0_ENABLED !== "true") {
    console.info(JSON.stringify({ event: "narrowed_worker_gate", status: "disabled" }));
    if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, "status=disabled\n");
    return;
  }
  if (!process.env.DATABASE_URL_UNPOOLED) throw new Error("Missing protected database connection");
  const hash = createHash("sha256").update(readFileSync(resolve(process.cwd(), "../../deploy/ai-filter-refresh/bundle/worker.mjs"))).digest("hex");
  const client = postgres(process.env.DATABASE_URL_UNPOOLED, { max: 1, prepare: false, onnotice: () => {} });
  try {
    const [row] = await client`WITH k AS (SELECT hashtextextended(${narrowedWorkerReadinessKey(hash)},0) AS n)
      SELECT EXISTS(SELECT 1 FROM pg_locks,k WHERE locktype='advisory' AND granted AND mode='ExclusiveLock'
        AND classid=((n>>32)&4294967295)::oid AND objid=(n&4294967295)::oid AND objsubid=1) AS ready`;
    if (!row?.ready) throw new Error("The exact standalone worker must be active before web promotion");
    console.info(JSON.stringify({ event: "narrowed_worker_gate", status: "ready", workerSha256: hash }));
    if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, "status=ready\n");
  } finally { await client.end({ timeout: 5 }); }
}
void main().catch(() => { console.error("Narrowed host worker readiness gate failed; production was not promoted"); process.exitCode = 1; });
