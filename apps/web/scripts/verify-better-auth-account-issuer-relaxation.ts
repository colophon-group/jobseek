import { writeFileSync } from "node:fs";

import dotenv from "dotenv";
import postgres from "postgres";

import { logExternalError } from "../src/lib/safe-external-error";

dotenv.config({ path: ".env.local", quiet: true });

class ContractError extends Error {}

type IssuerColumn = {
  dataType: string;
  notNull: boolean;
  hasDefault: boolean;
};

type ObjectEvidence = {
  accountPresent: boolean;
  legacyIndexPresent: boolean;
  compatibilityFunctionPresent: boolean;
  compatibilityTriggerPresent: boolean;
};

type AccountEvidence = {
  total: number;
  blankPrimaryIds: number;
  blankAccountIds: number;
  blankProviderIds: number;
  blankUserIds: number;
  duplicateProviderAccounts: number;
};

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new ContractError(message);
}

const cliArgs = process.argv.slice(2).filter((argument) => argument !== "--");
const mode = cliArgs[0];
const outputPath = cliArgs[1];

function writeEvidence(evidence: Record<string, unknown>): void {
  const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
  if (outputPath) {
    writeFileSync(outputPath, rendered, { encoding: "utf8", mode: 0o600 });
  }
  process.stdout.write(rendered);
}

let capturedEvidence: Record<string, unknown> | undefined;

async function main(): Promise<void> {
  invariant(
    mode === "drift" && cliArgs.length <= 2,
    "Usage: tsx scripts/verify-better-auth-account-issuer-relaxation.ts drift [output.json]",
  );

  const databaseUrl = process.env.DATABASE_URL_UNPOOLED;
  invariant(
    databaseUrl,
    "DATABASE_URL_UNPOOLED must be set for production verification",
  );
  invariant(
    new URL(databaseUrl).port !== "6543",
    "Refusing production verification through the transaction pooler",
  );

  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: {
      application_name: "jobseek-better-auth-account-issuer-relaxation-drift",
    },
  });

  try {
    const evidence = await sql.begin(async (tx) => {
      await tx`SET TRANSACTION READ ONLY`;
      await tx`SET LOCAL statement_timeout = '30s'`;
      await tx`SET LOCAL idle_in_transaction_session_timeout = '60s'`;

      const [objects] = await tx<ObjectEvidence[]>`
        SELECT
          to_regclass('public.account') IS NOT NULL AS "accountPresent",
          to_regclass('public.account_issuer_account_id_uidx') IS NOT NULL
            AS "legacyIndexPresent",
          to_regprocedure(
            'public.jobseek_better_auth_account_issuer_compat()'
          ) IS NOT NULL AS "compatibilityFunctionPresent",
          EXISTS (
            SELECT 1
            FROM pg_trigger trigger
            JOIN pg_class relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relname = 'account'
              AND trigger.tgname = 'account_issuer_compat_before_write'
              AND NOT trigger.tgisinternal
          ) AS "compatibilityTriggerPresent"
      `;
      invariant(objects?.accountPresent, "public.account is absent");

      const issuerColumns = await tx<IssuerColumn[]>`
        SELECT
          data_type AS "dataType",
          is_nullable = 'NO' AS "notNull",
          column_default IS NOT NULL AS "hasDefault"
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'account'
          AND column_name = 'issuer'
      `;
      const [accounts] = await tx<AccountEvidence[]>`
        SELECT
          count(*)::integer AS total,
          count(*) FILTER (WHERE btrim(coalesce(id, '')) = '')::integer
            AS "blankPrimaryIds",
          count(*) FILTER (WHERE btrim(coalesce(account_id, '')) = '')::integer
            AS "blankAccountIds",
          count(*) FILTER (WHERE btrim(coalesce(provider_id, '')) = '')::integer
            AS "blankProviderIds",
          count(*) FILTER (WHERE btrim(coalesce(user_id, '')) = '')::integer
            AS "blankUserIds",
          (
            SELECT count(*)::integer
            FROM (
              SELECT provider_id, account_id
              FROM public.account
              GROUP BY provider_id, account_id
              HAVING count(*) > 1
            ) duplicate_identities
          ) AS "duplicateProviderAccounts"
        FROM public.account
      `;

      const issuerColumn = issuerColumns[0];
      const issuerContractExact = Boolean(
        issuerColumns.length === 1 &&
          issuerColumn?.dataType === "text" &&
          issuerColumn.notNull === false &&
          issuerColumn.hasDefault === false,
      );
      const legacyObjectsAbsent = Boolean(
        !objects.legacyIndexPresent &&
          !objects.compatibilityFunctionPresent &&
          !objects.compatibilityTriggerPresent,
      );
      const accountIdentitiesClean = Boolean(
        accounts &&
          accounts.blankPrimaryIds === 0 &&
          accounts.blankAccountIds === 0 &&
          accounts.blankProviderIds === 0 &&
          accounts.blankUserIds === 0 &&
          accounts.duplicateProviderAccounts === 0,
      );

      const baseEvidence = {
        checkedAt: new Date().toISOString(),
        mode,
        status: "checking",
        issuerColumn: {
          present: issuerColumns.length === 1,
          count: issuerColumns.length,
          dataType: issuerColumn?.dataType ?? null,
          notNull: issuerColumn?.notNull ?? null,
          hasDefault: issuerColumn?.hasDefault ?? null,
          contractExact: issuerContractExact,
        },
        legacyObjects: {
          indexPresent: objects.legacyIndexPresent,
          compatibilityFunctionPresent: objects.compatibilityFunctionPresent,
          compatibilityTriggerPresent: objects.compatibilityTriggerPresent,
          absent: legacyObjectsAbsent,
        },
        accounts,
        accountIdentitiesClean,
      };
      capturedEvidence = baseEvidence;

      invariant(issuerContractExact, "Expected issuer to be nullable text without a default");
      invariant(legacyObjectsAbsent, "Legacy Better Auth issuer objects are still present");
      invariant(accounts, "Could not audit account identities");
      invariant(accountIdentitiesClean, "Better Auth account identities are invalid or duplicated");

      return { ...baseEvidence, status: "passed" };
    });

    writeEvidence(evidence);
  } finally {
    await sql.end({ timeout: 5 });
  }
}

void main().catch((error: unknown) => {
  writeEvidence({
    checkedAt: new Date().toISOString(),
    mode: mode === "drift" ? mode : null,
    status: "failed",
    failure:
      error instanceof ContractError
        ? error.message
        : "Unexpected Better Auth issuer relaxation verification failure",
    ...(capturedEvidence ? { audit: capturedEvidence } : {}),
  });
  logExternalError(
    "error",
    {
      service: "database",
      operation: "verify_better_auth_account_issuer_relaxation",
    },
    error,
  );
  process.exitCode = 1;
});
