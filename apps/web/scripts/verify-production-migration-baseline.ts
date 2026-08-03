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
    "Usage: tsx scripts/verify-production-migration-baseline.ts <preflight|postflight|drift> [output.json]",
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

const migrationCreatedAt = 1_785_750_000_000;
const migrationPath = resolve(
  process.cwd(),
  "drizzle/0083_reconcile_supabase_baseline.sql",
);
const migrationHash = createHash("sha256")
  .update(readFileSync(migrationPath))
  .digest("hex");

const verifiedPreflight = {
  rowCount: 72,
  ledgerDigest: "90545f8ccec9ae7a8cb21742c842e8d8",
  latestCreatedAt: 1_779_148_800_000,
  latestHash:
    "a5bcf949b24c1a7f90cb458db9b52366e8cf5ce4ebd3338242502a1701e16c42",
} as const;

const expectedIndexDefinition =
  "CREATE INDEX idx_jp_active_company ON public.job_posting USING btree (company_id) WHERE (is_active = true)";
const expectedInterviewTypes = [
  "interview",
  "phone_screen",
  "video_call",
  "technical",
  "coding",
  "system_design",
  "behavioral",
  "onsite",
  "panel",
  "hiring_manager",
  "other",
] as const;

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

async function main() {
  const sql = postgres(verifiedDatabaseUrl, {
    max: 1,
    prepare: false,
    connection: { application_name: `jobseek-web-migration-${mode}` },
  });

  try {
    const evidence = await sql.begin(async (tx) => {
    await tx`SET TRANSACTION READ ONLY`;
    await tx`SET LOCAL statement_timeout = '30s'`;

    const [database] = await tx<
      { serverVersion: string; databaseBytes: string }[]
    >`
      SELECT
        current_setting('server_version') AS "serverVersion",
        pg_database_size(current_database())::text AS "databaseBytes"
    `;
    const [ledger] = await tx<
      {
        rowCount: number;
        ledgerDigest: string;
        latestCreatedAt: string;
        latestHash: string;
      }[]
    >`
      SELECT
        count(*)::integer AS "rowCount",
        md5(
          string_agg(
            id::text || ':' || hash || ':' || created_at::text,
            ',' ORDER BY id
          )
        ) AS "ledgerDigest",
        (array_agg(created_at::text ORDER BY created_at DESC, id DESC))[1]
          AS "latestCreatedAt",
        (array_agg(hash ORDER BY created_at DESC, id DESC))[1]
          AS "latestHash"
      FROM drizzle.__drizzle_migrations
    `;
    const reconcileRows = await tx<{ count: number }[]>`
      SELECT count(*)::integer AS count
      FROM drizzle.__drizzle_migrations
      WHERE created_at = ${migrationCreatedAt}
        AND hash = ${migrationHash}
    `;
    const experienceColumns = await tx<
      { columnName: string; dataType: string }[]
    >`
      SELECT
        attname AS "columnName",
        format_type(atttypid, atttypmod) AS "dataType"
      FROM pg_attribute
      WHERE attrelid = 'public.job_posting'::regclass
        AND attname IN ('experience_min', 'experience_max')
        AND NOT attisdropped
      ORDER BY attname
    `;
    const [interview] = await tx<{ definition: string | null }[]>`
      SELECT pg_get_constraintdef(oid, true) AS definition
      FROM pg_constraint
      WHERE conrelid = 'public.application_interview'::regclass
        AND conname = 'application_interview_type_check'
    `;
    const [watchlistDefault] = await tx<{ value: string | null }[]>`
      SELECT pg_get_expr(adbin, adrelid) AS value
      FROM pg_attrdef
      WHERE adrelid = 'public.watchlist'::regclass
        AND adnum = (
          SELECT attnum
          FROM pg_attribute
          WHERE attrelid = 'public.watchlist'::regclass
            AND attname = 'is_public'
            AND NOT attisdropped
        )
    `;
    const [activeCompanyIndex] = await tx<
      {
        definition: string | null;
        isValid: boolean | null;
        isReady: boolean | null;
      }[]
    >`
      SELECT
        pg_get_indexdef(indexrelid) AS definition,
        indisvalid AS "isValid",
        indisready AS "isReady"
      FROM pg_index
      WHERE indexrelid = to_regclass('public.idx_jp_active_company')
    `;
    const [watchlists] = await tx<
      { total: number; publicCount: number; privateCount: number }[]
    >`
      SELECT
        count(*)::integer AS total,
        count(*) FILTER (WHERE is_public)::integer AS "publicCount",
        count(*) FILTER (WHERE NOT is_public)::integer AS "privateCount"
      FROM public.watchlist
    `;

    assert(database, "Could not read production database metadata");
    assert(ledger, "Could not read the Drizzle migration ledger");
    assert(
      experienceColumns.length === 2 &&
        experienceColumns.every((column) => column.dataType === "integer"),
      `Expected the two Supabase experience columns to remain integer; got ${JSON.stringify(experienceColumns)}`,
    );
    assert(interview?.definition, "Interview type CHECK constraint is missing");
    for (const interviewType of expectedInterviewTypes) {
      assert(
        interview.definition.includes(`'${interviewType}'::text`),
        `Interview type CHECK is missing ${interviewType}`,
      );
    }
    assert(
      activeCompanyIndex?.isValid === true &&
        activeCompanyIndex.isReady === true &&
        activeCompanyIndex.definition === expectedIndexDefinition,
      `Active-company index differs from the verified definition: ${JSON.stringify(activeCompanyIndex)}`,
    );
    assert(watchlists, "Could not read watchlist visibility counts");

    if (mode === "preflight") {
      assert(
        ledger.rowCount === verifiedPreflight.rowCount &&
          ledger.ledgerDigest === verifiedPreflight.ledgerDigest &&
          Number(ledger.latestCreatedAt) === verifiedPreflight.latestCreatedAt &&
          ledger.latestHash === verifiedPreflight.latestHash,
        `Production migration ledger is not the audited 0079 baseline: ${JSON.stringify(ledger)}`,
      );
      assert(
        watchlistDefault?.value === "true",
        `Expected the unreconciled watchlist default to be true; got ${watchlistDefault?.value}`,
      );
      assert(
        reconcileRows[0]?.count === 0,
        "0083 is already recorded; use postflight or drift verification",
      );
    } else {
      assert(
        reconcileRows[0]?.count === 1,
        "The exact 0083 reconciliation hash is not recorded once",
      );
      assert(
        watchlistDefault?.value === "false",
        `Expected the reconciled watchlist default to be false; got ${watchlistDefault?.value}`,
      );
      if (mode === "postflight") {
        assert(
          ledger.rowCount === verifiedPreflight.rowCount + 1 &&
            Number(ledger.latestCreatedAt) === migrationCreatedAt &&
            ledger.latestHash === migrationHash,
          `Unexpected immediate post-migration ledger state: ${JSON.stringify(ledger)}`,
        );
      }
    }

    return {
      checkedAt: new Date().toISOString(),
      mode,
      serverVersion: database.serverVersion,
      databaseBytes: database.databaseBytes,
      ledger,
      reconcileMigration: {
        createdAt: migrationCreatedAt,
        hash: migrationHash,
        rows: reconcileRows[0]?.count ?? 0,
      },
      schema: {
        experienceColumns,
        interviewConstraint: interview.definition,
        watchlistDefault: watchlistDefault?.value ?? null,
        activeCompanyIndex,
      },
      watchlists,
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
  console.error("Production migration verification failed:", error);
  process.exitCode = 1;
});
