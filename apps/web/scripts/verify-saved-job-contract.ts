import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import postgres from "postgres";

import { isExactSavedJobTextCheck } from "./saved-job-contract-definition";

dotenv.config({ path: ".env.local", quiet: true });

type Mode = "preflight" | "postflight" | "drift";

const cliArgs = process.argv.slice(2).filter((argument) => argument !== "--");
const mode = cliArgs[0] as Mode | undefined;
const outputPath = cliArgs[1];
if (mode !== "preflight" && mode !== "postflight" && mode !== "drift") {
  throw new Error(
    "Usage: tsx scripts/verify-saved-job-contract.ts <preflight|postflight|drift> [output.json]",
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
const contractCreatedAt = 1_785_757_200_000;
const migrationHash = (filename: string) =>
  createHash("sha256")
    .update(readFileSync(resolve(process.cwd(), "drizzle", filename)))
    .digest("hex");
const repairHash = migrationHash("0083_reconcile_supabase_baseline.sql");
const expandHash = migrationHash("0084_saved_job_snapshot_expand.sql");
const contractHash = migrationHash("0085_saved_job_snapshot_contract.sql");
const verifiedLegacyLedger = {
  rowCount: 72,
  digest: "90545f8ccec9ae7a8cb21742c842e8d8",
} as const;
const requiredColumns = [
  "company_id",
  "company_name",
  "company_slug",
  "posting_first_seen_at",
  "posting_is_active",
  "posting_source_url",
  "posting_title",
] as const;
const optionalColumns = [
  "company_icon",
  "posting_salary_currency",
  "posting_salary_max",
  "posting_salary_min",
  "posting_salary_period",
] as const;
const snapshotColumns = [...requiredColumns, ...optionalColumns].sort();
const snapshotColumnTypes: Record<string, string> = {
  company_icon: "text",
  company_id: "uuid",
  company_name: "text",
  company_slug: "text",
  posting_first_seen_at: "timestamp with time zone",
  posting_is_active: "boolean",
  posting_salary_currency: "text",
  posting_salary_max: "integer",
  posting_salary_min: "integer",
  posting_salary_period: "text",
  posting_source_url: "text",
  posting_title: "text",
};

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

async function main() {
  const sql = postgres(verifiedDatabaseUrl, {
    max: 1,
    prepare: false,
    connection: { application_name: `jobseek-saved-job-contract-${mode}` },
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
        { repair: number; expand: number; contract: number }[]
      >`
        SELECT
          count(*) FILTER (
            WHERE created_at = ${repairCreatedAt} AND hash = ${repairHash}
          )::integer AS repair,
          count(*) FILTER (
            WHERE created_at = ${expandCreatedAt} AND hash = ${expandHash}
          )::integer AS expand,
          count(*) FILTER (
            WHERE created_at = ${contractCreatedAt} AND hash = ${contractHash}
          )::integer AS contract
        FROM drizzle.__drizzle_migrations
      `;
      const columns = await tx<
        {
          name: string;
          dataType: string;
          notNull: boolean;
          defaultValue: string | null;
        }[]
      >`
        SELECT
          attribute.attname AS name,
          format_type(attribute.atttypid, attribute.atttypmod) AS "dataType",
          attribute.attnotnull AS "notNull",
          pg_get_expr(default_value.adbin, default_value.adrelid) AS "defaultValue"
        FROM pg_attribute AS attribute
        LEFT JOIN pg_attrdef AS default_value
          ON default_value.adrelid = attribute.attrelid
         AND default_value.adnum = attribute.attnum
        WHERE attribute.attrelid = 'public.saved_job'::regclass
          AND attribute.attname IN ${tx(snapshotColumns)}
          AND NOT attribute.attisdropped
        ORDER BY attribute.attname
      `;
      const [jobPostingId] = await tx<
        { dataType: string; notNull: boolean; defaultValue: string | null }[]
      >`
        SELECT
          format_type(attribute.atttypid, attribute.atttypmod) AS "dataType",
          attribute.attnotnull AS "notNull",
          pg_get_expr(default_value.adbin, default_value.adrelid) AS "defaultValue"
        FROM pg_attribute AS attribute
        LEFT JOIN pg_attrdef AS default_value
          ON default_value.adrelid = attribute.attrelid
         AND default_value.adnum = attribute.attnum
        WHERE attribute.attrelid = 'public.saved_job'::regclass
          AND attribute.attname = 'job_posting_id'
          AND NOT attribute.attisdropped
      `;
      const [postingForeignKey] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count
        FROM pg_constraint
        WHERE conrelid = 'public.saved_job'::regclass
          AND contype = 'f'
          AND (
            conname = 'saved_job_job_posting_id_job_posting_id_fk'
            OR conkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'job_posting_id'
                  AND NOT attisdropped
              )
            ]::smallint[]
          )
      `;
      const checks = await tx<
        { name: string; validated: boolean; definition: string }[]
      >`
        SELECT
          conname AS name,
          convalidated AS validated,
          pg_get_constraintdef(oid, true) AS definition
        FROM pg_constraint
        WHERE conrelid = 'public.saved_job'::regclass
          AND conname IN (
            'saved_job_required_snapshot_check',
            'saved_job_snapshot_text_nonblank_check'
          )
          AND contype = 'c'
        ORDER BY conname
      `;
      const [compatibility] = await tx<
        { triggers: number; functions: number }[]
      >`
        SELECT
          (
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid = 'public.saved_job'::regclass
              AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
              AND tgenabled = 'O'
              AND NOT tgisinternal
          )::integer AS triggers,
          (
            SELECT count(*)
            FROM pg_proc
            WHERE oid = to_regprocedure('public.saved_job_snapshot_from_mirror()')
          )::integer AS functions
      `;
      const [durableRelations] = await tx<
        { userForeignKeys: number; uniqueIndexes: number; interviewForeignKeys: number }[]
      >`
        SELECT
          (
            SELECT count(*)
            FROM pg_constraint AS constraint_row
            WHERE constraint_row.conrelid = 'public.saved_job'::regclass
              AND constraint_row.confrelid = 'public.user'::regclass
              AND constraint_row.conname = 'saved_job_user_id_user_id_fk'
              AND constraint_row.contype = 'f'
              AND constraint_row.convalidated
              AND constraint_row.confdeltype = 'c'
              AND constraint_row.confupdtype = 'a'
              AND constraint_row.conkey = ARRAY[
                (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'user_id'
                    AND NOT attisdropped
                )
              ]::smallint[]
              AND constraint_row.confkey = ARRAY[
                (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'public.user'::regclass
                    AND attname = 'id'
                    AND NOT attisdropped
                )
              ]::smallint[]
          )::integer AS "userForeignKeys",
          (
            SELECT count(*)
            FROM pg_index
            WHERE indexrelid = to_regclass('public.idx_sj_user_posting')
              AND indrelid = 'public.saved_job'::regclass
              AND indisunique
              AND indisvalid
              AND indisready
              AND indpred IS NULL
              AND indexprs IS NULL
              AND indnkeyatts = 2
              AND indkey::text = format(
                '%s %s',
                (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'user_id'
                    AND NOT attisdropped
                ),
                (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'job_posting_id'
                    AND NOT attisdropped
                )
              )
          )::integer AS "uniqueIndexes",
          (
            SELECT count(*)
            FROM pg_constraint
            WHERE conrelid = 'public.application_interview'::regclass
              AND confrelid = 'public.saved_job'::regclass
              AND conname =
                'application_interview_saved_job_id_fkey'
              AND contype = 'f'
              AND convalidated
              AND confdeltype = 'c'
              AND confupdtype = 'a'
              AND conkey = ARRAY[
                (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'public.application_interview'::regclass
                    AND attname = 'saved_job_id'
                    AND NOT attisdropped
                )
              ]::smallint[]
              AND confkey = ARRAY[
                (
                  SELECT attnum
                  FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'id'
                    AND NOT attisdropped
                )
              ]::smallint[]
          )::integer AS "interviewForeignKeys"
      `;
      const [snapshots] = await tx<
        { total: number; incompleteRequired: number }[]
      >`
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
      `;

      assert(legacyLedger, "Could not read the legacy migration ledger");
      assert(ledger, "Could not read the migration ledger");
      assert(migrationRows, "Could not read the saved-job migration ledger rows");
      assert(jobPostingId, "saved_job.job_posting_id is absent");
      assert(postingForeignKey, "Could not audit the posting FK");
      assert(compatibility, "Could not audit compatibility objects");
      assert(durableRelations, "Could not audit durable saved-job relationships");
      assert(snapshots, "Could not audit saved-job snapshots");
      assert(
        legacyLedger.rowCount === verifiedLegacyLedger.rowCount &&
          legacyLedger.digest === verifiedLegacyLedger.digest,
        `Legacy migration ledger drifted: ${JSON.stringify(legacyLedger)}`,
      );
      assert(migrationRows.repair === 1, "The exact 0083 repair is not recorded once");
      assert(migrationRows.expand === 1, "The exact 0084 expand is not recorded once");
      assert(
        columns.length === snapshotColumns.length &&
          columns.every((column, index) => column.name === snapshotColumns[index]) &&
          columns.every(
            (column) => column.dataType === snapshotColumnTypes[column.name],
          ) &&
          columns.every((column) => column.defaultValue === null),
        `Saved-job snapshot columns differ: ${JSON.stringify(columns)}`,
      );
      assert(
        jobPostingId.dataType === "uuid" &&
          jobPostingId.notNull &&
          jobPostingId.defaultValue === null,
        `job_posting_id must remain default-free uuid NOT NULL: ${JSON.stringify(jobPostingId)}`,
      );
      assert(
        durableRelations.userForeignKeys === 1 &&
          durableRelations.uniqueIndexes === 1 &&
          durableRelations.interviewForeignKeys === 1,
        `Durable saved-job relationships differ: ${JSON.stringify(durableRelations)}`,
      );
      assert(
        snapshots.incompleteRequired === 0,
        `${snapshots.incompleteRequired} saved jobs have incomplete required snapshots`,
      );

      let source: { total: number; incompleteRequired: number } | null = null;
      if (mode === "preflight") {
        [source] = await tx<{ total: number; incompleteRequired: number }[]>`
          SELECT
            count(*)::integer AS total,
            count(*) FILTER (
              WHERE NULLIF(btrim(posting.titles[1]), '') IS NULL
                 OR NULLIF(btrim(posting.source_url), '') IS NULL
                 OR posting.first_seen_at IS NULL
                 OR posting.is_active IS NULL
                 OR source_company.id IS NULL
                 OR NULLIF(btrim(source_company.name), '') IS NULL
                 OR NULLIF(btrim(source_company.slug), '') IS NULL
            )::integer AS "incompleteRequired"
          FROM public.saved_job AS saved
          LEFT JOIN public.job_posting AS posting
            ON posting.id = saved.job_posting_id
          LEFT JOIN public.company AS source_company
            ON source_company.id = posting.company_id
        `;
        const [exactPostingForeignKey] = await tx<
          { count: number; deleteAction: string | null; validated: boolean | null }[]
        >`
          SELECT
            count(*)::integer AS count,
            min(confdeltype::text) AS "deleteAction",
            bool_and(convalidated) AS validated
          FROM pg_constraint
          WHERE conrelid = 'public.saved_job'::regclass
            AND confrelid = 'public.job_posting'::regclass
            AND conname = 'saved_job_job_posting_id_job_posting_id_fk'
            AND contype = 'f'
            AND conkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'job_posting_id'
                  AND NOT attisdropped
              )
            ]::smallint[]
            AND confkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.job_posting'::regclass
                  AND attname = 'id'
                  AND NOT attisdropped
              )
            ]::smallint[]
        `;

        assert(
          ledger.rowCount === 74 &&
            Number(ledger.latestCreatedAt) === expandCreatedAt &&
            ledger.latestHash === expandHash,
          `Expected the exact post-0084 ledger before contract: ${JSON.stringify(ledger)}`,
        );
        assert(migrationRows.contract === 0, "0085 is already recorded");
        assert(
          columns.every((column) => !column.notNull),
          `Expected every expand column to remain nullable: ${JSON.stringify(columns)}`,
        );
        assert(
          checks.length === 1 &&
            checks[0]?.name === "saved_job_required_snapshot_check" &&
            checks[0].validated,
          `Expected the exact validated temporary CHECK: ${JSON.stringify(checks)}`,
        );
        assert(
          compatibility.triggers === 1 && compatibility.functions === 1,
          `Expected the compatibility trigger/function pair: ${JSON.stringify(compatibility)}`,
        );
        assert(
          postingForeignKey.count === 1 &&
            exactPostingForeignKey?.count === 1 &&
            exactPostingForeignKey.deleteAction === "r" &&
            exactPostingForeignKey.validated === true,
          `Expected the validated restrictive posting FK: ${JSON.stringify(exactPostingForeignKey)}`,
        );
        assert(source, "Could not audit saved-job mirror sources");
        assert(
          source.total === snapshots.total && source.incompleteRequired === 0,
          `Saved-job mirror sources are incomplete: ${JSON.stringify(source)}`,
        );
      } else {
        const required = new Set(requiredColumns);
        assert(migrationRows.contract === 1, "The exact 0085 contract is not recorded once");
        assert(
          columns.every((column) => column.notNull === required.has(
            column.name as (typeof requiredColumns)[number],
          )),
          `Required/optional saved-job nullability differs: ${JSON.stringify(columns)}`,
        );
        assert(
          checks.length === 1 &&
            checks[0]?.name === "saved_job_snapshot_text_nonblank_check" &&
            checks[0].validated &&
            isExactSavedJobTextCheck(checks[0].definition),
          `Expected the permanent validated text CHECK: ${JSON.stringify(checks)}`,
        );
        assert(
          compatibility.triggers === 0 && compatibility.functions === 0,
          `Compatibility objects remain: ${JSON.stringify(compatibility)}`,
        );
        assert(
          postingForeignKey.count === 0,
          "The saved_job posting FK still exists after contract",
        );
        if (mode === "postflight") {
          assert(
            ledger.rowCount === 75 &&
              Number(ledger.latestCreatedAt) === contractCreatedAt &&
              ledger.latestHash === contractHash,
            `Unexpected immediate post-0085 ledger: ${JSON.stringify(ledger)}`,
          );
        }
      }

      return {
        checkedAt: new Date().toISOString(),
        mode,
        legacyLedger,
        ledger,
        migrationRows,
        migrations: {
          expand: { createdAt: expandCreatedAt, hash: expandHash },
          contract: { createdAt: contractCreatedAt, hash: contractHash },
        },
        columns,
        jobPostingId,
        postingForeignKeys: postingForeignKey.count,
        checks,
        compatibility,
        durableRelations,
        source,
        snapshots,
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
  console.error("Saved-job contract verification failed:", error);
  process.exitCode = 1;
});
