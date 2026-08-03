import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import postgres from "postgres";

dotenv.config({ path: ".env.local", quiet: true });

type Mode = "preflight" | "postflight" | "drift";

const cliArgs = process.argv.slice(2).filter((argument) => argument !== "--");
const mode = cliArgs[0] as Mode | undefined;
const outputPath = cliArgs[1];
if (mode !== "preflight" && mode !== "postflight" && mode !== "drift") {
  throw new Error(
    "Usage: tsx scripts/verify-saved-job-expand.ts <preflight|postflight|drift> [output.json]",
  );
}

const databaseUrl = process.env.DATABASE_URL_UNPOOLED;
if (!databaseUrl) {
  throw new Error("DATABASE_URL_UNPOOLED must be set for production verification");
}
if (new URL(databaseUrl).port === "6543") {
  throw new Error("Refusing production verification through the transaction pooler");
}
const verifiedDatabaseUrl = databaseUrl;

const repairCreatedAt = 1_785_750_000_000;
const expandCreatedAt = 1_785_753_600_000;
const migrationHash = (filename: string) =>
  createHash("sha256")
    .update(readFileSync(resolve(process.cwd(), "drizzle", filename)))
    .digest("hex");
const repairHash = migrationHash("0083_reconcile_supabase_baseline.sql");
const expandHash = migrationHash("0084_saved_job_snapshot_expand.sql");
const verifiedLegacyLedger = {
  rowCount: 72,
  digest: "90545f8ccec9ae7a8cb21742c842e8d8",
} as const;
const snapshotColumns = [
  "company_icon",
  "company_id",
  "company_name",
  "company_slug",
  "posting_first_seen_at",
  "posting_is_active",
  "posting_salary_currency",
  "posting_salary_max",
  "posting_salary_min",
  "posting_salary_period",
  "posting_source_url",
  "posting_title",
] as const;

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

async function main() {
  const sql = postgres(verifiedDatabaseUrl, {
    max: 1,
    prepare: false,
    connection: { application_name: `jobseek-saved-job-expand-${mode}` },
  });

  try {
    const evidence = await sql.begin(async (tx) => {
      await tx`SET TRANSACTION READ ONLY`;
      await tx`SET LOCAL statement_timeout = '30s'`;

      const [legacyLedger] = await tx<{ rowCount: number; digest: string }[]>`
        SELECT
          count(*)::integer AS "rowCount",
          md5(
            string_agg(
              id::text || ':' || hash || ':' || created_at::text,
              ',' ORDER BY id
            )
          ) AS digest
        FROM drizzle.__drizzle_migrations
        WHERE created_at <= 1779148800000
      `;
      const [ledger] = await tx<
        { rowCount: number; latestCreatedAt: string; latestHash: string }[]
      >`
        SELECT
          count(*)::integer AS "rowCount",
          (array_agg(created_at::text ORDER BY created_at DESC, id DESC))[1]
            AS "latestCreatedAt",
          (array_agg(hash ORDER BY created_at DESC, id DESC))[1]
            AS "latestHash"
        FROM drizzle.__drizzle_migrations
      `;
      const [migrationRows] = await tx<
        { repair: number; expand: number }[]
      >`
        SELECT
          count(*) FILTER (
            WHERE created_at = ${repairCreatedAt} AND hash = ${repairHash}
          )::integer AS repair,
          count(*) FILTER (
            WHERE created_at = ${expandCreatedAt} AND hash = ${expandHash}
          )::integer AS expand
        FROM drizzle.__drizzle_migrations
      `;
      const columns = await tx<{ name: string }[]>`
        SELECT attname AS name
        FROM pg_attribute
        WHERE attrelid = 'public.saved_job'::regclass
          AND attname IN ${tx(snapshotColumns)}
          AND NOT attisdropped
        ORDER BY attname
      `;
      const [foreignKey] = await tx<
        { count: number; deleteAction: string | null; validated: boolean | null }[]
      >`
        SELECT
          count(*)::integer AS count,
          min(confdeltype::text) AS "deleteAction",
          bool_and(convalidated) AS validated
        FROM pg_constraint
        WHERE conrelid = 'public.saved_job'::regclass
          AND confrelid = 'public.job_posting'::regclass
          AND contype = 'f'
      `;
      const [requiredCheck] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count
        FROM pg_constraint
        WHERE conrelid = 'public.saved_job'::regclass
          AND conname = 'saved_job_required_snapshot_check'
          AND contype = 'c'
          AND convalidated
      `;
      const [trigger] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count
        FROM pg_trigger
        WHERE tgrelid = 'public.saved_job'::regclass
          AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
          AND tgenabled = 'O'
          AND NOT tgisinternal
      `;
      const [source] = await tx<
        { total: number; incompleteRequired: number }[]
      >`
        SELECT
          count(*)::integer AS total,
          count(*) FILTER (
            WHERE NULLIF(btrim(jp.titles[1]), '') IS NULL
               OR NULLIF(btrim(jp.source_url), '') IS NULL
               OR jp.first_seen_at IS NULL
               OR jp.is_active IS NULL
               OR c.id IS NULL
               OR NULLIF(btrim(c.name), '') IS NULL
               OR NULLIF(btrim(c.slug), '') IS NULL
          )::integer AS "incompleteRequired"
        FROM public.saved_job AS sj
        LEFT JOIN public.job_posting AS jp ON jp.id = sj.job_posting_id
        LEFT JOIN public.company AS c ON c.id = jp.company_id
      `;

      const actualColumns = columns.map((column) => column.name);
      const snapshots = actualColumns.length === snapshotColumns.length
        ? (await tx<{ total: number; incompleteRequired: number }[]>`
            SELECT
              count(*)::integer AS total,
              count(*) FILTER (
                WHERE NULLIF(btrim(posting_title), '') IS NULL
                   OR NULLIF(btrim(posting_source_url), '') IS NULL
                   OR posting_first_seen_at IS NULL
                   OR posting_is_active IS NULL
                   OR company_id IS NULL
                   OR NULLIF(btrim(company_name), '') IS NULL
                   OR NULLIF(btrim(company_slug), '') IS NULL
              )::integer AS "incompleteRequired"
            FROM public.saved_job
          `)[0]
        : null;

      assert(legacyLedger, "Could not read the legacy migration ledger");
      assert(ledger, "Could not read the migration ledger");
      assert(migrationRows, "Could not read reconciliation/expand ledger rows");
      assert(foreignKey?.count === 1, "The saved_job → job_posting FK is not exact");
      assert(requiredCheck, "Could not audit the required snapshot CHECK");
      assert(source, "Could not audit saved-job sources");
      assert(
        legacyLedger.rowCount === verifiedLegacyLedger.rowCount &&
          legacyLedger.digest === verifiedLegacyLedger.digest,
        `Legacy migration ledger drifted: ${JSON.stringify(legacyLedger)}`,
      );
      assert(migrationRows.repair === 1, "The exact 0083 repair is not recorded once");
      assert(
        source.incompleteRequired === 0,
        `${source.incompleteRequired} saved jobs lack required mirror source fields`,
      );

      if (mode === "preflight") {
        assert(
          ledger.rowCount === 73 &&
            Number(ledger.latestCreatedAt) === repairCreatedAt &&
            ledger.latestHash === repairHash,
          `Expected the exact post-0083 ledger before expand: ${JSON.stringify(ledger)}`,
        );
        assert(migrationRows.expand === 0, "0084 is already recorded");
        assert(actualColumns.length === 0, "Snapshot columns already exist before 0084");
        assert(trigger?.count === 0, "Compatibility trigger exists before 0084");
        assert(
          foreignKey.deleteAction === "c" && foreignKey.validated === true,
          `Expected the validated cascading pre-expand FK: ${JSON.stringify(foreignKey)}`,
        );
        assert(requiredCheck.count === 0, "Required snapshot CHECK exists before 0084");
      } else {
        assert(migrationRows.expand === 1, "The exact 0084 expand is not recorded once");
        assert(
          actualColumns.length === snapshotColumns.length &&
            actualColumns.every((column, index) => column === snapshotColumns[index]),
          `Saved-job snapshot columns differ: ${JSON.stringify(actualColumns)}`,
        );
        assert(trigger?.count === 1, "Compatibility trigger is not enabled exactly once");
        assert(
          foreignKey.deleteAction === "r" && foreignKey.validated === true,
          `Expected the validated restrictive expand FK: ${JSON.stringify(foreignKey)}`,
        );
        assert(requiredCheck.count === 1, "Required snapshot CHECK is not validated once");
        assert(
          snapshots?.total === source.total && snapshots.incompleteRequired === 0,
          `Saved-job snapshots are incomplete: ${JSON.stringify(snapshots)}`,
        );
        if (mode === "postflight") {
          assert(
            ledger.rowCount === 74 &&
              Number(ledger.latestCreatedAt) === expandCreatedAt &&
              ledger.latestHash === expandHash,
            `Unexpected immediate post-0084 ledger: ${JSON.stringify(ledger)}`,
          );
        }
      }

      return {
        checkedAt: new Date().toISOString(),
        mode,
        legacyLedger,
        ledger,
        migrationRows,
        expand: { createdAt: expandCreatedAt, hash: expandHash },
        source,
        snapshots,
        snapshotColumns: actualColumns,
        postingForeignKey: foreignKey,
        requiredSnapshotChecks: requiredCheck.count,
        compatibilityTriggers: trigger?.count ?? 0,
      };
    });

    const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
    if (outputPath) writeFileSync(outputPath, rendered, { mode: 0o600 });
    process.stdout.write(rendered);
  } finally {
    await sql.end();
  }
}

void main().catch((error: unknown) => {
  console.error("Saved-job expand verification failed:", error);
  process.exitCode = 1;
});
