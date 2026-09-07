import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import postgres, { type Sql } from "postgres";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const migrationPath = resolve(
  webRoot,
  "drizzle/0088_notification_policy_foundation.sql",
);

const constrainedStatuses = [
  "skipped",
  "sent",
  "unknown",
  "quota_deferred",
] as const;

type ConstrainedStatus = (typeof constrainedStatuses)[number];

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function parseDatabaseUrl(argv: string[]): string {
  const flagIndex = argv.indexOf("--database-url");
  if (flagIndex === -1 || !argv[flagIndex + 1]) {
    throw new Error(
      "Usage: tsx scripts/test-notification-policy-pg17.ts --database-url <disposable-postgresql-url>",
    );
  }
  if (argv.indexOf("--database-url", flagIndex + 1) !== -1) {
    throw new Error("--database-url must be supplied exactly once");
  }

  const rawUrl = argv[flagIndex + 1];
  const parsed = new URL(rawUrl);
  if (!/^postgres(?:ql)?:$/.test(parsed.protocol)) {
    throw new Error("--database-url must use the postgres or postgresql protocol");
  }

  const databaseName = decodeURIComponent(parsed.pathname.replace(/^\//, ""));
  if (["postgres", "template0", "template1"].includes(databaseName)) {
    throw new Error("Refusing to use a PostgreSQL administrative database");
  }
  if (!/fixture/i.test(databaseName)) {
    throw new Error(
      "Refusing destructive fixture setup: disposable database name must contain 'fixture'",
    );
  }

  return rawUrl;
}

async function resetFixture(sql: Sql): Promise<void> {
  await sql.unsafe("DROP SCHEMA IF EXISTS public CASCADE");
  await sql.unsafe("CREATE SCHEMA public");
  await sql.unsafe(`
    CREATE TABLE public."user" (
      id text PRIMARY KEY,
      name text NOT NULL
    );
    CREATE TABLE public.user_preferences (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id text NOT NULL UNIQUE REFERENCES public."user"(id) ON DELETE CASCADE
    );
    CREATE TABLE public.watchlist (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id text NOT NULL REFERENCES public."user"(id) ON DELETE CASCADE,
      alerts_enabled boolean DEFAULT false NOT NULL
    );
    INSERT INTO public."user" (id, name)
    VALUES ('notification-fixture-user', 'Notification Fixture User');
  `);
}

async function applyRealMigration(sql: Sql): Promise<void> {
  const migration = await readFile(migrationPath, "utf8");
  const statements = migration
    .split("--> statement-breakpoint")
    .map((statement) => statement.trim())
    .filter(Boolean);

  invariant(statements.length > 0, "0088 migration contains no statements");
  await sql.begin(async (transaction) => {
    for (const statement of statements) {
      await transaction.unsafe(statement);
    }
  });
}

function expectedConstraint(status: ConstrainedStatus): string {
  return status === "skipped"
    ? "notification_delivery_skipped_check"
    : "notification_delivery_sendable_match_check";
}

async function insertDelivery(
  sql: Sql,
  status: ConstrainedStatus,
  ordinal: number,
  matchCount: number | null,
): Promise<void> {
  const scheduledFor = new Date(Date.UTC(2026, 8, 7 + ordinal, 8));
  const windowEnd = new Date(scheduledFor.getTime() - 24 * 60 * 60 * 1_000);
  const windowStart = new Date(scheduledFor.getTime() - 8 * 24 * 60 * 60 * 1_000);
  const hasProviderAttempt = status === "sent" || status === "unknown";
  const completedAt = status === "sent" || status === "skipped"
    ? scheduledFor
    : null;
  const deferredUntil = status === "quota_deferred"
    ? new Date(scheduledFor.getTime() + 24 * 60 * 60 * 1_000)
    : null;
  const idempotencyKey = [
    "notification-fixture",
    status,
    matchCount === null ? "null" : "valid",
  ].join(":");

  await sql`
    INSERT INTO public.notification_delivery (
      user_id,
      cadence,
      scheduled_for,
      window_start,
      window_end,
      status,
      match_count,
      idempotency_key,
      provider_message_id,
      provider_attempt_count,
      last_provider_attempt_at,
      deferred_until,
      completed_at
    ) VALUES (
      'notification-fixture-user',
      'weekly',
      ${scheduledFor},
      ${windowStart},
      ${windowEnd},
      ${status},
      ${matchCount},
      ${idempotencyKey},
      ${status === "sent" ? `provider-${ordinal}` : null},
      ${hasProviderAttempt ? 1 : 0},
      ${hasProviderAttempt ? windowEnd : null},
      ${deferredUntil},
      ${completedAt}
    )
  `;
}

async function assertNullMatchRejected(
  sql: Sql,
  status: ConstrainedStatus,
  ordinal: number,
): Promise<void> {
  try {
    await insertDelivery(sql, status, ordinal, null);
  } catch (error) {
    const pgError = error as {
      code?: string;
      constraint_name?: string;
    };
    invariant(
      pgError.code === "23514",
      `${status}: expected PostgreSQL check violation, got ${String(pgError.code)}`,
    );
    invariant(
      pgError.constraint_name === expectedConstraint(status),
      `${status}: expected ${expectedConstraint(status)}, got ${String(pgError.constraint_name)}`,
    );
    return;
  }

  throw new Error(`${status}: NULL match_count unexpectedly passed its check`);
}

async function runHarness(databaseUrl: string): Promise<void> {
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: { application_name: "jobseek-notification-policy-pg17-fixture" },
  });

  try {
    const [server] = await sql<{ version: number; databaseName: string }[]>`
      SELECT
        current_setting('server_version_num')::integer AS version,
        current_database() AS "databaseName"
    `;
    invariant(server, "Could not read PostgreSQL server identity");
    invariant(
      server.version >= 170_000 && server.version < 180_000,
      `Execution harness requires PostgreSQL 17, got server_version_num=${server.version}`,
    );

    const requestedDatabase = decodeURIComponent(
      new URL(databaseUrl).pathname.replace(/^\//, ""),
    );
    invariant(
      server.databaseName === requestedDatabase,
      "Connected database does not match the explicitly supplied URL",
    );

    await resetFixture(sql);
    await applyRealMigration(sql);

    for (const [ordinal, status] of constrainedStatuses.entries()) {
      await assertNullMatchRejected(sql, status, ordinal);
      await insertDelivery(sql, status, ordinal, status === "skipped" ? 0 : 1);
    }

    const rows = await sql<{ status: ConstrainedStatus; matchCount: number }[]>`
      SELECT status, match_count AS "matchCount"
      FROM public.notification_delivery
      ORDER BY status
    `;
    invariant(
      rows.length === constrainedStatuses.length,
      `Expected ${constrainedStatuses.length} valid delivery rows, found ${rows.length}`,
    );
    for (const row of rows) {
      invariant(
        row.matchCount === (row.status === "skipped" ? 0 : 1),
        `${row.status}: valid match_count did not persist`,
      );
    }

    console.log(
      "PASS notification delivery NULL match_count guards and valid status shapes",
    );
  } finally {
    await sql.end({ timeout: 5 });
  }
}

async function main(): Promise<void> {
  const databaseUrl = parseDatabaseUrl(process.argv.slice(2));
  await runHarness(databaseUrl);
  console.log("PostgreSQL 17 notification policy execution harness passed.");
}

void main().catch((error: unknown) => {
  const message = error instanceof Error
    ? error.message
    : "Unknown notification policy harness failure";
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
