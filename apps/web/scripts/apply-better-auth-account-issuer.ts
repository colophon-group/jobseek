import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import { readMigrationFiles, type MigrationMeta } from "drizzle-orm/migrator";
import postgres from "postgres";

import { logExternalError } from "../src/lib/safe-external-error";

dotenv.config({ path: ".env.local", quiet: true });

const migrationFolder = resolve(process.cwd(), "drizzle");
const targetTag = "0087_better_auth_account_issuer";
const prerequisiteTag = "0086_drop_supabase_job_posting";
const targetCreatedAt = 1_787_560_116_000;
const prerequisiteCreatedAt = 1_785_760_800_000;

interface Journal {
  entries: Array<{ idx: number; when: number; tag: string }>;
}

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function migrationByTag(
  journal: Journal,
  migrations: MigrationMeta[],
  tag: string,
): MigrationMeta {
  const index = journal.entries.findIndex((entry) => entry.tag === tag);
  invariant(index !== -1, `Migration journal does not contain ${tag}`);
  const migration = migrations[index];
  invariant(migration, `Migration metadata does not contain ${tag}`);
  invariant(
    migration.folderMillis === journal.entries[index]?.when,
    `Migration timestamp does not match journal entry ${tag}`,
  );
  return migration;
}

async function main(): Promise<void> {
  const databaseUrl = process.env.DATABASE_URL_UNPOOLED;
  invariant(databaseUrl, "DATABASE_URL_UNPOOLED must be set");
  invariant(
    process.env.MIGRATION_REQUIRE_UNPOOLED === "true",
    "MIGRATION_REQUIRE_UNPOOLED=true is required",
  );
  invariant(
    new URL(databaseUrl).port !== "6543",
    "Refusing to migrate through the Supabase transaction pooler",
  );

  const journal = JSON.parse(
    readFileSync(resolve(migrationFolder, "meta/_journal.json"), "utf8"),
  ) as Journal;
  const migrations = readMigrationFiles({ migrationsFolder: migrationFolder });
  invariant(
    journal.entries.length === migrations.length,
    "Journal and SQL migration counts differ",
  );

  const prerequisite = migrationByTag(journal, migrations, prerequisiteTag);
  const target = migrationByTag(journal, migrations, targetTag);
  invariant(
    prerequisite.folderMillis === prerequisiteCreatedAt,
    "0086 prerequisite timestamp differs",
  );
  invariant(target.folderMillis === targetCreatedAt, "0087 target timestamp differs");

  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: { application_name: "jobseek-better-auth-issuer-migration" },
  });

  try {
    const outcome = await sql.begin(async (tx) => {
      await tx`SET LOCAL lock_timeout = '10s'`;
      await tx`SET LOCAL statement_timeout = '10min'`;
      await tx`SET LOCAL idle_in_transaction_session_timeout = '2min'`;
      await tx`
        SELECT pg_advisory_xact_lock(
          hashtextextended('jobseek:web-schema-migrations', 0)
        )
      `;

      const [ledger] = await tx<
        { rowCount: number; latestCreatedAt: string | null; latestHash: string | null }[]
      >`
        SELECT
          count(*)::integer AS "rowCount",
          (array_agg(created_at::text ORDER BY created_at DESC, id DESC))[1]
            AS "latestCreatedAt",
          (array_agg(hash ORDER BY created_at DESC, id DESC))[1]
            AS "latestHash"
        FROM drizzle.__drizzle_migrations
      `;
      const [targetLedger] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count
        FROM drizzle.__drizzle_migrations
        WHERE created_at = ${target.folderMillis}
          AND hash = ${target.hash}
      `;
      invariant(ledger && targetLedger, "Could not read migration ledger");

      if (targetLedger.count === 1) {
        return "already-applied" as const;
      }
      invariant(targetLedger.count === 0, "0087 is recorded more than once");
      invariant(
        ledger.rowCount === 76 &&
          Number(ledger.latestCreatedAt) === prerequisite.folderMillis &&
          ledger.latestHash === prerequisite.hash,
        `Expected exact post-0086 ledger before 0087: ${JSON.stringify(ledger)}`,
      );

      for (const statement of target.sql) {
        if (statement.trim()) await tx.unsafe(statement);
      }
      await tx`
        INSERT INTO drizzle.__drizzle_migrations (hash, created_at)
        VALUES (${target.hash}, ${target.folderMillis})
      `;
      return "applied" as const;
    });

    process.stdout.write(`${JSON.stringify({ migration: targetTag, outcome })}\n`);
  } finally {
    await sql.end({ timeout: 5 });
  }
}

void main().catch((error: unknown) => {
  logExternalError(
    "error",
    { service: "database", operation: "apply_better_auth_account_issuer" },
    error,
  );
  process.exitCode = 1;
});
