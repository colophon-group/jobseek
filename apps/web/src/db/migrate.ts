import { createHash } from "node:crypto";
import dotenv from "dotenv";
dotenv.config({ path: ".env.local", quiet: true });
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import { logExternalError } from "@/lib/safe-external-error";

const unpooledUrl = process.env.DATABASE_URL_UNPOOLED;
const url = unpooledUrl ?? process.env.DATABASE_URL;
if (!url) {
  throw new Error("DATABASE_URL_UNPOOLED or DATABASE_URL must be set");
}
if (process.env.MIGRATION_REQUIRE_UNPOOLED === "true" && !unpooledUrl) {
  throw new Error(
    "MIGRATION_REQUIRE_UNPOOLED=true requires DATABASE_URL_UNPOOLED",
  );
}

const parsedUrl = new URL(url);
if (
  process.env.MIGRATION_REQUIRE_UNPOOLED === "true" &&
  parsedUrl.port === "6543"
) {
  throw new Error("Refusing to migrate through the Supabase transaction pooler");
}

const lockSql = postgres(url, {
  max: 1,
  prepare: false,
  connect_timeout: 15,
  connection: { application_name: "jobseek-web-migration-lock" },
});
const migrationSql = postgres(url, {
  max: 1,
  prepare: false,
  connect_timeout: 15,
  connection: { application_name: "jobseek-web-migrations" },
});

type RetirementAttestationMode = "production-drop" | "restore-drill";

function requiredRetirementEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for retirement attestation`);
  return value;
}

function parseRunId(name: string): number {
  const raw = requiredRetirementEnvironment(name);
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw))) {
    throw new Error(`${name} must be a safe non-negative integer`);
  }
  return Number(raw);
}

async function prepareRetirementAttestation(): Promise<void> {
  const rawMode = process.env.RETIREMENT_ATTESTATION_MODE;
  if (!rawMode) return;
  if (rawMode !== "production-drop" && rawMode !== "restore-drill") {
    throw new Error("RETIREMENT_ATTESTATION_MODE is invalid");
  }
  const mode: RetirementAttestationMode = rawMode;
  const confirmation = requiredRetirementEnvironment("RETIREMENT_CONFIRMATION");
  const backupRestoreRunId = parseRunId("RETIREMENT_BACKUP_RESTORE_RUN_ID");
  const crawlerDeployRunId = parseRunId("RETIREMENT_CRAWLER_DEPLOY_RUN_ID");
  const typesenseBackfillRunId = parseRunId(
    "RETIREMENT_TYPESENSE_BACKFILL_RUN_ID",
  );
  const webDeploySha = requiredRetirementEnvironment("RETIREMENT_WEB_DEPLOY_SHA");
  const readinessDigest = requiredRetirementEnvironment(
    "RETIREMENT_READINESS_DIGEST",
  );
  if (!/^[0-9a-f]{40}$/.test(webDeploySha)) {
    throw new Error("RETIREMENT_WEB_DEPLOY_SHA must be a lowercase 40-hex SHA");
  }
  if (!/^[0-9a-f]{64}$/.test(readinessDigest)) {
    throw new Error("RETIREMENT_READINESS_DIGEST must be a lowercase SHA-256");
  }

  const expectedConfirmation =
    mode === "production-drop"
      ? "DROP-ONLY-JOB-POSTING-0086"
      : "RESTORE-ONLY-JOB-POSTING-0086";
  if (confirmation !== expectedConfirmation) {
    throw new Error("RETIREMENT_CONFIRMATION does not match the attestation mode");
  }
  if (
    mode === "production-drop" &&
    (backupRestoreRunId <= 0 ||
      crawlerDeployRunId <= 0 ||
      typesenseBackfillRunId <= 0)
  ) {
    throw new Error("Production retirement requires three positive evidence run IDs");
  }
  if (
    mode === "restore-drill" &&
    (backupRestoreRunId !== 0 ||
      crawlerDeployRunId !== 0 ||
      typesenseBackfillRunId !== 0)
  ) {
    throw new Error("Restore-only retirement requires zero production run IDs");
  }

  const canonical = [
    mode,
    confirmation,
    String(backupRestoreRunId),
    String(crawlerDeployRunId),
    String(typesenseBackfillRunId),
    webDeploySha,
  ].join("\n");
  const expectedDigest = createHash("sha256").update(canonical).digest("hex");
  if (readinessDigest !== expectedDigest) {
    throw new Error("RETIREMENT_READINESS_DIGEST does not bind the evidence inputs");
  }

  await migrationSql`
    CREATE TEMP TABLE jobseek_retirement_attestation (
      mode text NOT NULL,
      confirmation text NOT NULL,
      backup_restore_run_id bigint NOT NULL,
      crawler_deploy_run_id bigint NOT NULL,
      typesense_backfill_run_id bigint NOT NULL,
      web_deploy_sha text NOT NULL,
      readiness_digest text NOT NULL,
      attested_at timestamp with time zone NOT NULL
    ) ON COMMIT PRESERVE ROWS
  `;
  await migrationSql`
    INSERT INTO pg_temp.jobseek_retirement_attestation (
      mode,
      confirmation,
      backup_restore_run_id,
      crawler_deploy_run_id,
      typesense_backfill_run_id,
      web_deploy_sha,
      readiness_digest,
      attested_at
    ) VALUES (
      ${mode},
      ${confirmation},
      ${backupRestoreRunId},
      ${crawlerDeployRunId},
      ${typesenseBackfillRunId},
      ${webDeploySha},
      ${readinessDigest},
      clock_timestamp()
    )
  `;
}

async function main() {
  let lockConnection:
    | Awaited<ReturnType<typeof lockSql.reserve>>
    | undefined;
  let advisoryLockHeld = false;

  try {
    // Drizzle requires the root postgres.js client because migrations call
    // client.begin(). A reserved client has neither begin() nor options and
    // cannot be passed to drizzle(). Keep a separate reserved session solely
    // for the advisory lock while the one-connection migration pool owns DDL.
    lockConnection = await lockSql.reserve();
    const [lock] = await lockConnection<{ acquired: boolean }[]>`
      SELECT pg_try_advisory_lock(
        hashtextextended('jobseek:web-schema-migrations', 0)
      ) AS acquired
    `;
    advisoryLockHeld = lock?.acquired === true;
    if (!advisoryLockHeld) {
      throw new Error("Another web schema migration is already running");
    }

    // migrationSql has max=1, so these session settings and the Drizzle
    // transaction use the same physical connection.
    await migrationSql`SET lock_timeout = '10s'`;
    await migrationSql`SET statement_timeout = '10min'`;
    await migrationSql`SET idle_in_transaction_session_timeout = '2min'`;
    await prepareRetirementAttestation();

    const db = drizzle(migrationSql);
    console.log("Running migrations with the web schema advisory lock...");
    await migrate(db, { migrationsFolder: "./drizzle" });
    console.log("Migrations complete.");
  } finally {
    try {
      if (lockConnection && advisoryLockHeld) {
        const [unlock] = await lockConnection<{ released: boolean }[]>`
          SELECT pg_advisory_unlock(
            hashtextextended('jobseek:web-schema-migrations', 0)
          ) AS released
        `;
        if (unlock?.released !== true) {
          console.error("Migration advisory lock release was not confirmed");
          process.exitCode = 1;
        }
      }
    } finally {
      try {
        if (lockConnection) await lockConnection.release();
      } finally {
        await Promise.all([
          lockSql.end({ timeout: 5 }),
          migrationSql.end({ timeout: 5 }),
        ]);
      }
    }
  }
}

void main().catch((err: unknown) => {
  logExternalError("error", { service: "database", operation: "migrate" }, err);
  process.exitCode = 1;
});
