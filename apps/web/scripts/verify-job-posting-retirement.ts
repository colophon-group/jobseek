import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import postgres from "postgres";
import { logExternalError } from "../src/lib/safe-external-error";

import { isExactSavedJobTextCheck } from "./saved-job-contract-definition";

dotenv.config({ path: ".env.local", quiet: true });

type Mode = "preflight" | "postflight" | "drift";

const cliArgs = process.argv.slice(2).filter((argument) => argument !== "--");
const mode = cliArgs[0] as Mode | undefined;
const outputPath = cliArgs[1];
if (mode !== "preflight" && mode !== "postflight" && mode !== "drift") {
  throw new Error(
    "Usage: tsx scripts/verify-job-posting-retirement.ts <preflight|postflight|drift> [output.json]",
  );
}

const configuredDatabaseUrl = process.env.DATABASE_URL_UNPOOLED;
if (!configuredDatabaseUrl) {
  throw new Error("DATABASE_URL_UNPOOLED must be set for production verification");
}
const databaseUrl: string = configuredDatabaseUrl;
if (new URL(databaseUrl).port === "6543") {
  throw new Error("Refusing production verification through the transaction pooler");
}

const contractCreatedAt = 1_785_757_200_000;
const retirementCreatedAt = 1_785_760_800_000;
const accountIssuerCreatedAt = 1_787_560_116_000;
const freePlanSafetyBytes = 400 * 1024 * 1024;
const migrationHash = (filename: string) =>
  createHash("sha256")
    .update(readFileSync(resolve(process.cwd(), "drizzle", filename)))
    .digest("hex");
const contractHash = migrationHash("0085_saved_job_snapshot_contract.sql");
const retirementHash = migrationHash("0086_drop_supabase_job_posting.sql");
const accountIssuerHash = migrationHash("0087_better_auth_account_issuer.sql");
const requiredSnapshotColumns = [
  "company_id",
  "company_name",
  "company_slug",
  "job_posting_id",
  "posting_first_seen_at",
  "posting_is_active",
  "posting_source_url",
  "posting_title",
] as const;
const optionalSnapshotColumns = [
  "company_icon",
  "posting_salary_currency",
  "posting_salary_max",
  "posting_salary_min",
  "posting_salary_period",
] as const;
const snapshotColumns = [
  ...requiredSnapshotColumns,
  ...optionalSnapshotColumns,
].sort();
const snapshotColumnTypes: Record<string, string> = {
  company_icon: "text",
  company_id: "uuid",
  company_name: "text",
  company_slug: "text",
  job_posting_id: "uuid",
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

type RetirementSourceEvidence = {
  rows: string;
  relationBytes: string;
  projectedDatabaseBytes: string;
  inboundForeignKeys: number;
  dependentViews: number;
  dependentFunctions: number;
  noninternalTriggers: number;
  publications: number;
};

function failureMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown retirement verification failure";
}

async function main() {
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connection: { application_name: `jobseek-job-posting-retirement-${mode}` },
  });
  let capturedEvidence: Record<string, unknown> | undefined;

  try {
    const evidence = await sql.begin(async (tx) => {
      await tx`SET TRANSACTION READ ONLY`;
      await tx`SET LOCAL statement_timeout = '60s'`;

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
        { contract: number; retirement: number; accountIssuer: number }[]
      >`
        SELECT
          count(*) FILTER (
            WHERE created_at = ${contractCreatedAt} AND hash = ${contractHash}
          )::integer AS contract,
          count(*) FILTER (
            WHERE created_at = ${retirementCreatedAt} AND hash = ${retirementHash}
          )::integer AS retirement,
          count(*) FILTER (
            WHERE created_at = ${accountIssuerCreatedAt} AND hash = ${accountIssuerHash}
          )::integer AS "accountIssuer"
        FROM drizzle.__drizzle_migrations
      `;
      const [relation] = await tx<
        {
          oid: string | null;
          kind: string | null;
          persistence: string | null;
        }[]
      >`
        SELECT
          relation.oid::text AS oid,
          relation.relkind::text AS kind,
          relation.relpersistence::text AS persistence
        FROM (SELECT to_regclass('public.job_posting') AS oid) AS target
        LEFT JOIN pg_class AS relation ON relation.oid = target.oid
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
      const checks = await tx<
        { name: string; validated: boolean; definition: string }[]
      >`
        SELECT
          conname AS name,
          convalidated AS validated,
          pg_get_constraintdef(oid, true) AS definition
        FROM pg_constraint
        WHERE conrelid = 'public.saved_job'::regclass
          AND conname = 'saved_job_snapshot_text_nonblank_check'
      `;
      const [postingForeignKeys] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count
        FROM pg_constraint
        WHERE conrelid = 'public.saved_job'::regclass
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
      `;
      const [durableRelations] = await tx<
        {
          userForeignKeys: number;
          uniqueIndexes: number;
          interviewForeignKeys: number;
        }[]
      >`
        SELECT
          (
            SELECT count(*)
            FROM pg_constraint
            WHERE conrelid = 'public.saved_job'::regclass
              AND confrelid = 'public.user'::regclass
              AND conname = 'saved_job_user_id_user_id_fk'
              AND contype = 'f'
              AND convalidated
              AND confdeltype = 'c'
              AND confupdtype = 'a'
              AND conkey = ARRAY[
                (
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'user_id' AND NOT attisdropped
                )
              ]::smallint[]
              AND confkey = ARRAY[
                (
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = 'public.user'::regclass
                    AND attname = 'id' AND NOT attisdropped
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
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'user_id' AND NOT attisdropped
                ),
                (
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'job_posting_id' AND NOT attisdropped
                )
              )
          )::integer AS "uniqueIndexes",
          (
            SELECT count(*)
            FROM pg_constraint
            WHERE conrelid = 'public.application_interview'::regclass
              AND confrelid = 'public.saved_job'::regclass
              AND conname = 'application_interview_saved_job_id_fkey'
              AND contype = 'f'
              AND convalidated
              AND confdeltype = 'c'
              AND confupdtype = 'a'
              AND conkey = ARRAY[
                (
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = 'public.application_interview'::regclass
                    AND attname = 'saved_job_id' AND NOT attisdropped
                )
              ]::smallint[]
              AND confkey = ARRAY[
                (
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = 'public.saved_job'::regclass
                    AND attname = 'id' AND NOT attisdropped
                )
              ]::smallint[]
          )::integer AS "interviewForeignKeys"
      `;
      const [legacyReferences] = await tx<
        {
          compatibilityTriggers: number;
          compatibilityFunctions: number;
          referencingRoutines: number;
        }[]
      >`
        SELECT
          (
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid = 'public.saved_job'::regclass
              AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
              AND NOT tgisinternal
          )::integer AS "compatibilityTriggers",
          CASE
            WHEN to_regprocedure('public.saved_job_snapshot_from_mirror()') IS NULL
              THEN 0
            ELSE 1
          END::integer AS "compatibilityFunctions",
          (
            SELECT count(*)
            FROM pg_proc AS routine
            JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
            WHERE namespace.nspname <> 'information_schema'
              AND namespace.nspname !~ '^pg_'
              AND routine.prokind IN ('f', 'p')
              AND pg_get_functiondef(routine.oid) ~* '\mjob_posting\M'
          )::integer AS "referencingRoutines"
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
      const [database] = await tx<{ bytes: string }[]>`
        SELECT pg_database_size(current_database())::text AS bytes
      `;

      const preDropAudit =
        mode === "preflight" ||
        (mode === "drift" &&
          relation?.oid !== null &&
          migrationRows?.retirement === 0);
      let source: RetirementSourceEvidence | null = null;
      if (preDropAudit) {
        const sourceRows = await tx<RetirementSourceEvidence[]>`
          WITH target AS (
            SELECT 'public.job_posting'::regclass AS oid
          )
          SELECT
            (SELECT count(*)::text FROM public.job_posting) AS rows,
            pg_total_relation_size(target.oid)::text AS "relationBytes",
            (
              pg_database_size(current_database())
              - pg_total_relation_size(target.oid)
            )::text AS "projectedDatabaseBytes",
            (
              SELECT count(*) FROM pg_constraint
              WHERE confrelid = target.oid AND conrelid <> target.oid
            )::integer AS "inboundForeignKeys",
            (
              SELECT count(DISTINCT dependent_view.oid)
              FROM pg_depend AS dependency
              JOIN pg_rewrite AS rewrite_rule
                ON dependency.classid = 'pg_rewrite'::regclass
               AND dependency.objid = rewrite_rule.oid
              JOIN pg_class AS dependent_view
                ON dependent_view.oid = rewrite_rule.ev_class
              WHERE dependency.refclassid = 'pg_class'::regclass
                AND dependency.refobjid = target.oid
                AND dependent_view.oid <> target.oid
            )::integer AS "dependentViews",
            (
              SELECT count(DISTINCT dependency.objid)
              FROM pg_depend AS dependency
              WHERE dependency.refclassid = 'pg_class'::regclass
                AND dependency.refobjid = target.oid
                AND dependency.classid = 'pg_proc'::regclass
            )::integer AS "dependentFunctions",
            (
              SELECT count(*) FROM pg_trigger
              WHERE tgrelid = target.oid AND NOT tgisinternal
            )::integer AS "noninternalTriggers",
            (
              SELECT count(*) FROM pg_publication_rel
              WHERE prrelid = target.oid
            )::integer AS publications
          FROM target
        `;
        source = sourceRows[0] ?? null;
      }

      const baseEvidence = {
        checkedAt: new Date().toISOString(),
        mode,
        status: "checking",
        ledger,
        migrationRows,
        migrations: {
          contract: { createdAt: contractCreatedAt, hash: contractHash },
          retirement: { createdAt: retirementCreatedAt, hash: retirementHash },
        },
        relation,
        database: database
          ? { bytes: database.bytes, freePlanSafetyBytes }
          : null,
        source,
        columns,
        jobPostingId,
        postingForeignKeys: postingForeignKeys?.count,
        checks,
        durableRelations,
        legacyReferences,
        snapshots,
      };
      capturedEvidence = baseEvidence;

      assert(ledger, "Could not read the migration ledger");
      assert(migrationRows, "Could not read the retirement migration rows");
      assert(relation, "Could not audit public.job_posting");
      assert(jobPostingId, "saved_job.job_posting_id is absent");
      assert(postingForeignKeys, "Could not audit saved-job posting FKs");
      assert(durableRelations, "Could not audit durable saved-job relationships");
      assert(legacyReferences, "Could not audit legacy job_posting routines");
      assert(snapshots, "Could not audit saved-job snapshots");
      assert(database, "Could not read database size");
      assert(migrationRows.contract === 1, "The exact 0085 contract is not recorded once");
      const required = new Set<string>(requiredSnapshotColumns);
      assert(
        columns.length === snapshotColumns.length &&
          columns.every((column, index) => column.name === snapshotColumns[index]) &&
          columns.every(
            (column) => column.dataType === snapshotColumnTypes[column.name],
          ) &&
          columns.every((column) => column.notNull === required.has(column.name)) &&
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
        checks.length === 1 &&
          checks[0]?.validated &&
          isExactSavedJobTextCheck(checks[0].definition),
        `Saved-job snapshot CHECK differs: ${JSON.stringify(checks)}`,
      );
      assert(postingForeignKeys.count === 0, "A saved-job posting FK still exists");
      assert(
        durableRelations.userForeignKeys === 1 &&
          durableRelations.uniqueIndexes === 1 &&
          durableRelations.interviewForeignKeys === 1,
        `Durable saved-job relationships differ: ${JSON.stringify(durableRelations)}`,
      );
      assert(
        legacyReferences.compatibilityTriggers === 0 &&
          legacyReferences.compatibilityFunctions === 0 &&
          legacyReferences.referencingRoutines === 0,
        `Legacy job_posting routines remain: ${JSON.stringify(legacyReferences)}`,
      );
      assert(
        snapshots.incompleteRequired === 0,
        `${snapshots.incompleteRequired} saved jobs have incomplete required snapshots`,
      );

      const exactPreDropState =
        ledger.rowCount === 75 &&
        Number(ledger.latestCreatedAt) === contractCreatedAt &&
        ledger.latestHash === contractHash &&
        migrationRows.retirement === 0 &&
        relation.oid !== null &&
        relation.kind === "r" &&
        relation.persistence === "p";
      const postDropState = migrationRows.retirement === 1 && relation.oid === null;

      if (mode === "preflight" || (mode === "drift" && !postDropState)) {
        assert(
          exactPreDropState,
          `Expected the exact post-0085 pre-drop state: ${JSON.stringify({ ledger, migrationRows, relation })}`,
        );
        assert(source, "Could not audit the job_posting retirement source");
        assert(
          source.inboundForeignKeys === 0 &&
            source.dependentViews === 0 &&
            source.dependentFunctions === 0 &&
            source.noninternalTriggers === 0 &&
            source.publications === 0,
          `External job_posting dependencies remain: ${JSON.stringify(source)}`,
        );
        assert(
          Number(source.projectedDatabaseBytes) < freePlanSafetyBytes,
          `Projected database size is not below 400 MiB: ${source.projectedDatabaseBytes}`,
        );
      } else {
        assert(postDropState, "The exact 0086 post-drop state is absent");
        assert(
          Number(database.bytes) < freePlanSafetyBytes,
          `Database size is not below 400 MiB: ${database.bytes}`,
        );
        if (mode === "postflight") {
          assert(
            ledger.rowCount === 77 &&
              Number(ledger.latestCreatedAt) === accountIssuerCreatedAt &&
              ledger.latestHash === accountIssuerHash &&
              migrationRows.accountIssuer === 1,
            `Unexpected post-0087 ledger after retirement: ${JSON.stringify({ ledger, migrationRows })}`,
          );
        }
      }

      return { ...baseEvidence, status: "passed" };
    });

    const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
    if (outputPath) writeFileSync(outputPath, rendered, { mode: 0o600 });
    process.stdout.write(rendered);
  } catch (error: unknown) {
    const failureEvidence = {
      checkedAt: new Date().toISOString(),
      mode,
      status: "failed",
      failure: failureMessage(error),
      ...(capturedEvidence ? { audit: capturedEvidence } : {}),
    };
    const rendered = `${JSON.stringify(failureEvidence, null, 2)}\n`;
    if (outputPath) writeFileSync(outputPath, rendered, { mode: 0o600 });
    process.stdout.write(rendered);
    throw error;
  } finally {
    await sql.end();
  }
}

void main().catch((error: unknown) => {
  logExternalError(
    "error",
    { service: "database", operation: "verify_job_posting_retirement" },
    error,
  );
  process.exitCode = 1;
});
