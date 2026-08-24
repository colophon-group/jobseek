import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import postgres from "postgres";

import { logExternalError } from "../src/lib/safe-external-error";

dotenv.config({ path: ".env.local", quiet: true });

type Mode = "preflight" | "postflight" | "drift";

type LedgerEvidence = {
  rowCount: number;
  latestCreatedAt: string | null;
  latestHash: string | null;
};

type MigrationRowsEvidence = {
  prerequisiteExact: number;
  prerequisiteTimestamp: number;
  prerequisiteHash: number;
  targetExact: number;
  targetTimestamp: number;
  targetHash: number;
};

type AccountEvidence = {
  total: number;
  blankPrimaryIds: number;
  blankAccountIds: number;
  blankProviderIds: number;
  blankUserIds: number;
  unsupportedProviders: number;
  credentialIdentityMismatches: number;
  duplicateMappedIdentities: number;
  blankIssuers?: number;
  providerIssuerMismatches?: number;
  duplicateIssuerAccountIdentities?: number;
};

const cliArgs = process.argv.slice(2).filter((argument) => argument !== "--");
const requestedMode = cliArgs[0];
const outputPath = cliArgs[1];

const prerequisiteCreatedAt = 1_785_760_800_000;
const targetCreatedAt = 1_787_560_116_000;
const expectedFunctionSource = `
DECLARE
  expected_issuer text;
BEGIN
  expected_issuer := CASE NEW.provider_id
    WHEN 'credential' THEN 'local:credential'
    WHEN 'github' THEN 'local:oauth:github'
    WHEN 'google' THEN 'https://accounts.google.com'
    WHEN 'linkedin' THEN 'local:oauth:linkedin'
  END;

  IF expected_issuer IS NULL THEN
    RAISE EXCEPTION
      'Refusing Better Auth account write: unsupported provider_id %',
      NEW.provider_id;
  END IF;

  IF NEW.issuer IS NULL OR NULLIF(btrim(NEW.issuer), '') IS NULL THEN
    NEW.issuer := expected_issuer;
  ELSIF NEW.issuer IS DISTINCT FROM expected_issuer THEN
    RAISE EXCEPTION
      'Refusing Better Auth account write: issuer does not match provider_id %',
      NEW.provider_id;
  END IF;

  RETURN NEW;
END
`;

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function migrationHash(filename: string): string {
  return createHash("sha256")
    .update(readFileSync(resolve(process.cwd(), "drizzle", filename)))
    .digest("hex");
}

function normalizeFunctionSource(source: string): string {
  return source.replace(/\s+/g, " ").trim();
}

function failureMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Unknown Better Auth issuer verification failure";
}

function writeEvidence(evidence: Record<string, unknown>): void {
  const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
  if (outputPath) {
    writeFileSync(outputPath, rendered, { encoding: "utf8", mode: 0o600 });
  }
  process.stdout.write(rendered);
}

function isMode(value: string | undefined): value is Mode {
  return value === "preflight" || value === "postflight" || value === "drift";
}

let capturedEvidence: Record<string, unknown> | undefined;

async function main(): Promise<void> {
  invariant(
    isMode(requestedMode),
    "Usage: tsx scripts/verify-better-auth-account-issuer.ts <preflight|postflight|drift> [output.json]",
  );
  const mode = requestedMode;

  const databaseUrl = process.env.DATABASE_URL_UNPOOLED;
  invariant(
    databaseUrl,
    "DATABASE_URL_UNPOOLED must be set for production verification",
  );
  invariant(
    new URL(databaseUrl).port !== "6543",
    "Refusing production verification through the transaction pooler",
  );

  const prerequisiteHash = migrationHash("0086_drop_supabase_job_posting.sql");
  const targetHash = migrationHash("0087_better_auth_account_issuer.sql");
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: {
      application_name: `jobseek-better-auth-account-issuer-${mode}`,
    },
  });

  try {
    const evidence = await sql.begin(async (tx) => {
      await tx`SET TRANSACTION READ ONLY`;
      await tx`SET LOCAL statement_timeout = '30s'`;
      await tx`SET LOCAL idle_in_transaction_session_timeout = '60s'`;

      const [ledger] = await tx<LedgerEvidence[]>`
        SELECT
          count(*)::integer AS "rowCount",
          (array_agg(created_at::text ORDER BY created_at DESC, id DESC))[1]
            AS "latestCreatedAt",
          (array_agg(hash ORDER BY created_at DESC, id DESC))[1]
            AS "latestHash"
        FROM drizzle.__drizzle_migrations
      `;
      const [migrationRows] = await tx<MigrationRowsEvidence[]>`
        SELECT
          count(*) FILTER (
            WHERE created_at = ${prerequisiteCreatedAt}
              AND hash = ${prerequisiteHash}
          )::integer AS "prerequisiteExact",
          count(*) FILTER (
            WHERE created_at = ${prerequisiteCreatedAt}
          )::integer AS "prerequisiteTimestamp",
          count(*) FILTER (
            WHERE hash = ${prerequisiteHash}
          )::integer AS "prerequisiteHash",
          count(*) FILTER (
            WHERE created_at = ${targetCreatedAt}
              AND hash = ${targetHash}
          )::integer AS "targetExact",
          count(*) FILTER (
            WHERE created_at = ${targetCreatedAt}
          )::integer AS "targetTimestamp",
          count(*) FILTER (
            WHERE hash = ${targetHash}
          )::integer AS "targetHash"
        FROM drizzle.__drizzle_migrations
      `;
      const [accountRelation] = await tx<{ present: boolean }[]>`
        SELECT (to_regclass('public.account') IS NOT NULL) AS present
      `;
      const issuerColumns = await tx<
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
        WHERE attribute.attrelid = to_regclass('public.account')
          AND attribute.attname = 'issuer'
          AND NOT attribute.attisdropped
      `;
      const indexRows = await tx<
        {
          schemaName: string;
          tableName: string;
          method: string;
          unique: boolean;
          valid: boolean;
          ready: boolean;
          keyCount: number;
          attributeCount: number;
          hasPredicate: boolean;
          hasExpressions: boolean;
          keyColumns: string[];
        }[]
      >`
        SELECT
          namespace.nspname AS "schemaName",
          target.relname AS "tableName",
          method.amname AS method,
          index.indisunique AS unique,
          index.indisvalid AS valid,
          index.indisready AS ready,
          index.indnkeyatts::integer AS "keyCount",
          index.indnatts::integer AS "attributeCount",
          (index.indpred IS NOT NULL) AS "hasPredicate",
          (index.indexprs IS NOT NULL) AS "hasExpressions",
          ARRAY(
            SELECT pg_get_indexdef(index.indexrelid, position, true)
            FROM generate_series(1, index.indnkeyatts) AS position
            ORDER BY position
          )::text[] AS "keyColumns"
        FROM pg_index AS index
        JOIN pg_class AS index_relation ON index_relation.oid = index.indexrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = index_relation.relnamespace
        JOIN pg_class AS target ON target.oid = index.indrelid
        JOIN pg_am AS method ON method.oid = index_relation.relam
        WHERE index.indexrelid = to_regclass('public.account_issuer_account_id_uidx')
      `;
      const functionRows = await tx<
        {
          oid: string;
          kind: string;
          returnType: string;
          language: string;
          securityDefiner: boolean;
          volatility: string;
          configuration: string[] | null;
          source: string;
        }[]
      >`
        SELECT
          routine.oid::text AS oid,
          routine.prokind::text AS kind,
          format_type(routine.prorettype, NULL) AS "returnType",
          language.lanname AS language,
          routine.prosecdef AS "securityDefiner",
          routine.provolatile::text AS volatility,
          routine.proconfig AS configuration,
          routine.prosrc AS source
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        JOIN pg_language AS language ON language.oid = routine.prolang
        WHERE namespace.nspname = 'public'
          AND routine.proname = 'jobseek_better_auth_account_issuer_compat'
          AND routine.pronargs = 0
      `;
      const triggerRows = await tx<
        {
          enabled: string;
          internal: boolean;
          type: number;
          functionOid: string;
          updateColumns: string[];
        }[]
      >`
        SELECT
          trigger.tgenabled::text AS enabled,
          trigger.tgisinternal AS internal,
          trigger.tgtype::integer AS type,
          trigger.tgfoid::text AS "functionOid",
          ARRAY(
            SELECT attribute.attname
            FROM unnest(trigger.tgattr::smallint[]) WITH ORDINALITY
              AS update_attribute(attnum, position)
            JOIN pg_attribute AS attribute
              ON attribute.attrelid = trigger.tgrelid
             AND attribute.attnum = update_attribute.attnum
            ORDER BY update_attribute.position
          )::text[] AS "updateColumns"
        FROM pg_trigger AS trigger
        WHERE trigger.tgrelid = to_regclass('public.account')
          AND trigger.tgname = 'account_issuer_compat_before_write'
      `;

      let accounts: AccountEvidence | null = null;
      if (accountRelation?.present) {
        const baseRows = await tx<AccountEvidence[]>`
          WITH mapped AS (
            SELECT
              CASE provider_id
                WHEN 'credential' THEN 'local:credential'
                WHEN 'github' THEN 'local:oauth:github'
                WHEN 'google' THEN 'https://accounts.google.com'
                WHEN 'linkedin' THEN 'local:oauth:linkedin'
              END AS mapped_issuer,
              account_id
            FROM public.account
          ),
          duplicate_mapped AS (
            SELECT mapped_issuer, account_id
            FROM mapped
            WHERE mapped_issuer IS NOT NULL
            GROUP BY mapped_issuer, account_id
            HAVING count(*) > 1
          )
          SELECT
            count(*)::integer AS total,
            count(*) FILTER (
              WHERE NULLIF(btrim(id::text), '') IS NULL
            )::integer AS "blankPrimaryIds",
            count(*) FILTER (
              WHERE NULLIF(btrim(account_id::text), '') IS NULL
            )::integer AS "blankAccountIds",
            count(*) FILTER (
              WHERE NULLIF(btrim(provider_id::text), '') IS NULL
            )::integer AS "blankProviderIds",
            count(*) FILTER (
              WHERE NULLIF(btrim(user_id::text), '') IS NULL
            )::integer AS "blankUserIds",
            count(*) FILTER (
              WHERE provider_id NOT IN ('credential', 'github', 'google', 'linkedin')
            )::integer AS "unsupportedProviders",
            count(*) FILTER (
              WHERE provider_id = 'credential'
                AND account_id IS DISTINCT FROM user_id
            )::integer AS "credentialIdentityMismatches",
            (SELECT count(*)::integer FROM duplicate_mapped)
              AS "duplicateMappedIdentities"
          FROM public.account
        `;
        accounts = baseRows[0] ?? null;

        if (issuerColumns.length === 1 && accounts) {
          const [postMigrationData] = await tx<
            {
              blankIssuers: number;
              providerIssuerMismatches: number;
              duplicateIssuerAccountIdentities: number;
            }[]
          >`
            SELECT
              count(*) FILTER (
                WHERE NULLIF(btrim(issuer::text), '') IS NULL
              )::integer AS "blankIssuers",
              count(*) FILTER (
                WHERE issuer::text IS DISTINCT FROM CASE provider_id
                  WHEN 'credential' THEN 'local:credential'
                  WHEN 'github' THEN 'local:oauth:github'
                  WHEN 'google' THEN 'https://accounts.google.com'
                  WHEN 'linkedin' THEN 'local:oauth:linkedin'
                END
              )::integer AS "providerIssuerMismatches",
              (
                SELECT count(*)::integer
                FROM (
                  SELECT issuer, account_id
                  FROM public.account
                  GROUP BY issuer, account_id
                  HAVING count(*) > 1
                ) AS duplicates
              ) AS "duplicateIssuerAccountIdentities"
            FROM public.account
          `;
          invariant(postMigrationData, "Could not audit account issuer data");
          accounts = { ...accounts, ...postMigrationData };
        }
      }

      const issuerColumn = issuerColumns[0];
      const targetIndex = indexRows[0];
      const targetFunction = functionRows[0];
      const targetTrigger = triggerRows[0];
      const functionContractExact = Boolean(
        functionRows.length === 1 &&
          targetFunction?.kind === "f" &&
          targetFunction.returnType === "trigger" &&
          targetFunction.language === "plpgsql" &&
          !targetFunction.securityDefiner &&
          targetFunction.volatility === "v" &&
          targetFunction.configuration === null &&
          normalizeFunctionSource(targetFunction.source) ===
            normalizeFunctionSource(expectedFunctionSource),
      );
      const indexContractExact = Boolean(
        indexRows.length === 1 &&
          targetIndex?.schemaName === "public" &&
          targetIndex.tableName === "account" &&
          targetIndex.method === "btree" &&
          targetIndex.unique &&
          targetIndex.valid &&
          targetIndex.ready &&
          targetIndex.keyCount === 2 &&
          targetIndex.attributeCount === 2 &&
          !targetIndex.hasPredicate &&
          !targetIndex.hasExpressions &&
          targetIndex.keyColumns.length === 2 &&
          targetIndex.keyColumns[0] === "issuer" &&
          targetIndex.keyColumns[1] === "account_id",
      );
      const triggerContractExact = Boolean(
        triggerRows.length === 1 &&
          targetTrigger?.enabled === "O" &&
          !targetTrigger.internal &&
          targetTrigger.type === 23 &&
          targetTrigger.functionOid === targetFunction?.oid &&
          targetTrigger.updateColumns.length === 2 &&
          targetTrigger.updateColumns[0] === "provider_id" &&
          targetTrigger.updateColumns[1] === "issuer",
      );
      const columnContractExact = Boolean(
        issuerColumns.length === 1 &&
          issuerColumn?.dataType === "text" &&
          issuerColumn.notNull &&
          issuerColumn.defaultValue === null,
      );
      const prospectiveDataClean = Boolean(
        accounts &&
          accounts.blankPrimaryIds === 0 &&
          accounts.blankAccountIds === 0 &&
          accounts.blankProviderIds === 0 &&
          accounts.blankUserIds === 0 &&
          accounts.unsupportedProviders === 0 &&
          accounts.credentialIdentityMismatches === 0 &&
          accounts.duplicateMappedIdentities === 0,
      );
      const postMigrationDataClean = Boolean(
        prospectiveDataClean &&
          accounts?.blankIssuers === 0 &&
          accounts.providerIssuerMismatches === 0 &&
          accounts.duplicateIssuerAccountIdentities === 0,
      );
      const exactPreLedger = Boolean(
        ledger?.rowCount === 76 &&
          Number(ledger.latestCreatedAt) === prerequisiteCreatedAt &&
          ledger.latestHash === prerequisiteHash &&
          migrationRows?.prerequisiteExact === 1 &&
          migrationRows.prerequisiteTimestamp === 1 &&
          migrationRows.prerequisiteHash === 1 &&
          migrationRows.targetExact === 0 &&
          migrationRows.targetTimestamp === 0 &&
          migrationRows.targetHash === 0,
      );
      const exactPostLedger = Boolean(
        ledger?.rowCount === 77 &&
          Number(ledger.latestCreatedAt) === targetCreatedAt &&
          ledger.latestHash === targetHash &&
          migrationRows?.prerequisiteExact === 1 &&
          migrationRows.prerequisiteTimestamp === 1 &&
          migrationRows.prerequisiteHash === 1 &&
          migrationRows.targetExact === 1 &&
          migrationRows.targetTimestamp === 1 &&
          migrationRows.targetHash === 1,
      );
      const exactPreState = Boolean(
        accountRelation?.present &&
          exactPreLedger &&
          issuerColumns.length === 0 &&
          indexRows.length === 0 &&
          functionRows.length === 0 &&
          triggerRows.length === 0 &&
          prospectiveDataClean,
      );
      const exactPostState = Boolean(
        accountRelation?.present &&
          exactPostLedger &&
          columnContractExact &&
          indexContractExact &&
          functionContractExact &&
          triggerContractExact &&
          postMigrationDataClean,
      );

      const baseEvidence = {
        checkedAt: new Date().toISOString(),
        mode,
        status: "checking",
        migrations: {
          prerequisite: {
            tag: "0086_drop_supabase_job_posting",
            createdAt: prerequisiteCreatedAt,
            hash: prerequisiteHash,
          },
          target: {
            tag: "0087_better_auth_account_issuer",
            createdAt: targetCreatedAt,
            hash: targetHash,
          },
        },
        ledger,
        migrationRows,
        accountRelation: { present: accountRelation?.present ?? false },
        issuerColumn: {
          present: issuerColumns.length === 1,
          count: issuerColumns.length,
          dataType: issuerColumn?.dataType ?? null,
          notNull: issuerColumn?.notNull ?? null,
          hasDefault: issuerColumn ? issuerColumn.defaultValue !== null : null,
          contractExact: columnContractExact,
        },
        index: {
          present: indexRows.length === 1,
          count: indexRows.length,
          contractExact: indexContractExact,
        },
        compatibilityFunction: {
          present: functionRows.length === 1,
          count: functionRows.length,
          contractExact: functionContractExact,
        },
        compatibilityTrigger: {
          present: triggerRows.length === 1,
          count: triggerRows.length,
          enabled: targetTrigger?.enabled ?? null,
          contractExact: triggerContractExact,
        },
        accounts,
        states: { exactPreState, exactPostState },
      };
      capturedEvidence = baseEvidence;

      invariant(ledger, "Could not read the Drizzle migration ledger");
      invariant(migrationRows, "Could not audit the target migration rows");
      invariant(accountRelation?.present, "public.account is absent");
      invariant(accounts, "Could not audit account identities");

      if (mode === "preflight") {
        invariant(
          exactPreState || exactPostState,
          "Expected the exact clean post-0086 pre-state or exact 0087 post-state",
        );
      } else {
        invariant(exactPostState, "Expected the exact 0087 post-state");
      }

      return {
        ...baseEvidence,
        status: "passed",
        acceptedState: exactPostState ? "post-migration" : "pre-migration",
      };
    });

    writeEvidence(evidence);
  } finally {
    await sql.end({ timeout: 5 });
  }
}

void main().catch((error: unknown) => {
  writeEvidence({
    checkedAt: new Date().toISOString(),
    mode: isMode(requestedMode) ? requestedMode : null,
    status: "failed",
    failure: failureMessage(error),
    ...(capturedEvidence ? { audit: capturedEvidence } : {}),
  });
  logExternalError(
    "error",
    { service: "database", operation: "verify_better_auth_account_issuer" },
    error,
  );
  process.exitCode = 1;
});
