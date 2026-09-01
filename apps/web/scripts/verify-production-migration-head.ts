import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import { readMigrationFiles } from "drizzle-orm/migrator";
import postgres from "postgres";

import {
  assertMigrationHead,
  MigrationHeadMismatchError,
  type MigrationIdentity,
  type MigrationLedgerSnapshot,
} from "../src/db/migration-head";
import { logExternalError } from "../src/lib/safe-external-error";

dotenv.config({ path: ".env.local", quiet: true });

type LocalMigrationHead = MigrationIdentity & { tag: string };
type Journal = {
  entries: Array<{ tag: string; when: number }>;
};

function requireDatabaseUrl(): string {
  const value = process.env.DATABASE_URL_UNPOOLED;
  if (!value) {
    throw new Error(
      "DATABASE_URL_UNPOOLED must be set for migration-head verification",
    );
  }
  return value;
}

function loadLocalMigrationHead(): LocalMigrationHead {
  const migrationFolder = resolve(process.cwd(), "drizzle");
  const migrations = readMigrationFiles({ migrationsFolder: migrationFolder });
  const localHead = migrations.at(-1);
  if (!localHead) throw new Error("No local Drizzle migrations were found");

  const journal = JSON.parse(
    readFileSync(resolve(migrationFolder, "meta/_journal.json"), "utf8"),
  ) as Journal;
  const journalHead = journal.entries.at(-1);
  if (!journalHead || journalHead.when !== localHead.folderMillis) {
    throw new Error(
      "Local Drizzle journal head does not match the migration files",
    );
  }

  return {
    tag: journalHead.tag,
    createdAt: localHead.folderMillis,
    hash: localHead.hash,
  };
}

async function main(): Promise<void> {
  const databaseUrl = requireDatabaseUrl();
  if (new URL(databaseUrl).port === "6543") {
    throw new Error(
      "Refusing migration-head verification through the transaction pooler",
    );
  }
  const expected = loadLocalMigrationHead();
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: { application_name: "jobseek-web-migration-head-check" },
  });

  try {
    const observed = await sql.begin(async (tx) => {
      await tx`SET TRANSACTION READ ONLY`;
      await tx`SET LOCAL statement_timeout = '15s'`;

      const latestRows = await tx<
        Array<{ createdAt: string; hash: string }>
      >`
        SELECT created_at::text AS "createdAt", hash
        FROM drizzle.__drizzle_migrations
        ORDER BY created_at DESC, id DESC
        LIMIT 1
      `;
      const [counts] = await tx<
        Array<{ matchingHeadRows: number; headTimestampRows: number }>
      >`
        SELECT
          count(*) FILTER (
            WHERE created_at = ${expected.createdAt}
              AND hash = ${expected.hash}
          )::integer AS "matchingHeadRows",
          count(*) FILTER (
            WHERE created_at = ${expected.createdAt}
          )::integer AS "headTimestampRows"
        FROM drizzle.__drizzle_migrations
      `;

      const latest = latestRows[0];
      const snapshot: MigrationLedgerSnapshot = {
        latest: latest
          ? { createdAt: Number(latest.createdAt), hash: latest.hash }
          : null,
        matchingHeadRows: counts?.matchingHeadRows ?? 0,
        headTimestampRows: counts?.headTimestampRows ?? 0,
      };
      return snapshot;
    });

    assertMigrationHead(expected, observed);
    process.stdout.write(`${JSON.stringify({
      event: "production_migration_head_verified",
      migration: expected.tag,
      createdAt: expected.createdAt,
      hash: expected.hash.slice(0, 12),
    })}\n`);
  } finally {
    await sql.end({ timeout: 5 });
  }
}

void main().catch((error: unknown) => {
  if (error instanceof MigrationHeadMismatchError) {
    console.error(
      "Production migration head does not match the checked-out code. Refusing promotion; apply reviewed migrations and rerun the deployment.",
    );
  } else {
    logExternalError(
      "error",
      { service: "database", operation: "verify_migration_head" },
      error,
    );
  }
  process.exitCode = 1;
});
