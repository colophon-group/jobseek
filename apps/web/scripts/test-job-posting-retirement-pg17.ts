import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { readMigrationFiles, type MigrationMeta } from "drizzle-orm/migrator";
import postgres, { type Sql } from "postgres";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const migrationFolder = resolve(webRoot, "drizzle");
const migrationRunner = resolve(webRoot, "src/db/migrate.ts");
const tsxRunner = resolve(webRoot, "node_modules/tsx/dist/cli.mjs");

const retirementTag = "0086_drop_supabase_job_posting";
const productionConfirmation = "DROP-ONLY-JOB-POSTING-0086";
const restoreConfirmation = "RESTORE-ONLY-JOB-POSTING-0086";
const fixtureWebSha = "0123456789abcdef0123456789abcdef01234567";

type AttestationMode = "production-drop" | "restore-drill";

interface Journal {
  entries: Array<{
    idx: number;
    when: number;
    tag: string;
  }>;
}

interface LedgerRow {
  id: number;
  hash: string;
  createdAt: string;
}

interface FixtureProof {
  publicTables: string[];
  ledger: LedgerRow[];
  savedJobDigest: string;
  relationshipDigest: string;
  linkedRowCount: number;
  guardObjectDigest: string;
  guardObjectCount: number;
  jobPostingPresent: boolean;
}

interface MigrationResult {
  code: number | null;
  output: string;
}

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function assertEqual<T>(actual: T, expected: T, message: string): void {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson !== expectedJson) {
    throw new Error(`${message}\nexpected: ${expectedJson}\nactual:   ${actualJson}`);
  }
}

function sha256(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function parseDatabaseUrl(argv: string[]): string {
  const flagIndex = argv.indexOf("--database-url");
  if (flagIndex === -1 || !argv[flagIndex + 1]) {
    throw new Error(
      "Usage: tsx scripts/test-job-posting-retirement-pg17.ts --database-url <disposable-postgresql-url>",
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
  if (!/(?:retirement|fixture)/i.test(databaseName)) {
    throw new Error(
      "Refusing destructive fixture setup: disposable database name must contain 'retirement' or 'fixture'",
    );
  }
  if (["postgres", "template0", "template1"].includes(databaseName)) {
    throw new Error("Refusing to use a PostgreSQL administrative database");
  }

  return rawUrl;
}

async function loadLedgerFixture(): Promise<{
  seed: MigrationMeta[];
  retirement: MigrationMeta;
  subsequent: MigrationMeta[];
}> {
  const journal = JSON.parse(
    await readFile(resolve(migrationFolder, "meta/_journal.json"), "utf8"),
  ) as Journal;
  const migrations = readMigrationFiles({ migrationsFolder: migrationFolder });

  invariant(journal.entries.length === migrations.length, "Journal and SQL migration counts differ");
  invariant(migrations.length === 77, `Expected 77 real journal migrations, found ${migrations.length}`);

  const retirementIndex = journal.entries.findIndex((entry) => entry.tag === retirementTag);
  invariant(retirementIndex !== -1, `Journal does not contain ${retirementTag}`);
  const retirement = migrations[retirementIndex];
  invariant(retirement, "Retirement migration metadata is absent");
  const through0085 = migrations.slice(0, retirementIndex);
  const subsequent = migrations.slice(retirementIndex + 1);
  invariant(through0085.length === 74, "Expected 74 journal entries through 0085");
  invariant(subsequent.length === 2, "Expected exactly two journal entries after 0086");

  // The production guard intentionally expects 75 ledger rows at the 0085
  // tip, one more than the current 74 journal entries through 0085. Model that
  // historical extra row by repeating the oldest real migration. No synthetic
  // hash or timestamp is accepted into this execution fixture.
  const seed = [through0085[0], ...through0085];
  invariant(seed.length === 75, "Post-0085 ledger fixture must contain exactly 75 rows");
  invariant(seed.every(Boolean), "Ledger fixture contains an absent migration");
  invariant(seed.at(-1)?.folderMillis === 1_785_757_200_000, "Ledger fixture does not end at 0085");

  return { seed, retirement, subsequent };
}

const baseFixtureSql = String.raw`
  CREATE SCHEMA drizzle;
  CREATE TABLE drizzle.__drizzle_migrations (
    id serial PRIMARY KEY,
    hash text NOT NULL,
    created_at bigint
  );

  CREATE TABLE public."user" (
    id text PRIMARY KEY,
    name text NOT NULL
  );

  CREATE TABLE public.account (
    id text PRIMARY KEY,
    account_id text NOT NULL,
    provider_id text NOT NULL,
    user_id text NOT NULL
  );

  CREATE TABLE public.user_preferences (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id text NOT NULL UNIQUE,
    CONSTRAINT user_preferences_user_id_user_id_fk
      FOREIGN KEY (user_id) REFERENCES public."user"(id)
      ON DELETE CASCADE ON UPDATE NO ACTION
  );

  CREATE TABLE public.watchlist (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id text NOT NULL,
    alerts_enabled boolean DEFAULT false NOT NULL,
    CONSTRAINT watchlist_user_id_user_id_fk
      FOREIGN KEY (user_id) REFERENCES public."user"(id)
      ON DELETE CASCADE ON UPDATE NO ACTION
  );

  CREATE TABLE public.company (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    slug text NOT NULL
  );

  CREATE TABLE public.saved_job (
    id uuid PRIMARY KEY,
    user_id text NOT NULL,
    job_posting_id uuid NOT NULL,
    saved_at timestamp with time zone NOT NULL,
    posting_title text NOT NULL,
    posting_source_url text NOT NULL,
    posting_first_seen_at timestamp with time zone NOT NULL,
    posting_is_active boolean NOT NULL,
    posting_salary_min integer,
    posting_salary_max integer,
    posting_salary_currency text,
    posting_salary_period text,
    company_id uuid NOT NULL,
    company_name text NOT NULL,
    company_slug text NOT NULL,
    company_icon text,
    CONSTRAINT saved_job_user_id_user_id_fk
      FOREIGN KEY (user_id) REFERENCES public."user"(id)
      ON DELETE CASCADE ON UPDATE NO ACTION,
    CONSTRAINT saved_job_snapshot_text_nonblank_check
      CHECK (
        NULLIF(btrim(posting_title), '') IS NOT NULL
        AND NULLIF(btrim(posting_source_url), '') IS NOT NULL
        AND NULLIF(btrim(company_name), '') IS NOT NULL
        AND NULLIF(btrim(company_slug), '') IS NOT NULL
      )
  );

  CREATE UNIQUE INDEX idx_sj_user_posting
    ON public.saved_job (user_id, job_posting_id);

  CREATE TABLE public.application_interview (
    id uuid PRIMARY KEY,
    saved_job_id uuid NOT NULL,
    round smallint NOT NULL,
    CONSTRAINT application_interview_saved_job_id_fkey
      FOREIGN KEY (saved_job_id) REFERENCES public.saved_job(id)
      ON DELETE CASCADE ON UPDATE NO ACTION
  );

  CREATE TABLE public.retirement_fixture_control (
    id integer PRIMARY KEY,
    label text NOT NULL
  );

  INSERT INTO public."user" (id, name)
  VALUES ('fixture-user', 'Fixture User');

  INSERT INTO public.company (id, name, slug)
  VALUES
    ('10000000-0000-4000-8000-000000000001', 'Fixture One', 'fixture-one'),
    ('10000000-0000-4000-8000-000000000002', 'Fixture Two', 'fixture-two');

  INSERT INTO public.saved_job (
    id,
    user_id,
    job_posting_id,
    saved_at,
    posting_title,
    posting_source_url,
    posting_first_seen_at,
    posting_is_active,
    posting_salary_min,
    posting_salary_max,
    posting_salary_currency,
    posting_salary_period,
    company_id,
    company_name,
    company_slug,
    company_icon
  ) VALUES
    (
      '30000000-0000-4000-8000-000000000001',
      'fixture-user',
      '20000000-0000-4000-8000-000000000001',
      '2026-08-01T08:00:00Z',
      'Principal Fixture Engineer',
      'https://fixture.invalid/jobs/one',
      '2026-07-01T09:30:00Z',
      true,
      120000,
      160000,
      'CHF',
      'year',
      '10000000-0000-4000-8000-000000000001',
      'Fixture One',
      'fixture-one',
      'one.svg'
    ),
    (
      '30000000-0000-4000-8000-000000000002',
      'fixture-user',
      '20000000-0000-4000-8000-000000000002',
      '2026-08-02T08:00:00Z',
      'Fixture Reliability Engineer',
      'https://fixture.invalid/jobs/two',
      '2026-07-02T09:30:00Z',
      false,
      NULL,
      NULL,
      NULL,
      NULL,
      '10000000-0000-4000-8000-000000000002',
      'Fixture Two',
      'fixture-two',
      NULL
    );

  INSERT INTO public.application_interview (id, saved_job_id, round)
  VALUES
    (
      '40000000-0000-4000-8000-000000000001',
      '30000000-0000-4000-8000-000000000001',
      1
    ),
    (
      '40000000-0000-4000-8000-000000000002',
      '30000000-0000-4000-8000-000000000002',
      2
    );

  INSERT INTO public.retirement_fixture_control (id, label)
  VALUES (1, 'must survive 0086');
`;

const jobPostingFixtureSql = String.raw`
  CREATE TABLE public.job_posting (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL,
    titles text[] NOT NULL,
    source_url text NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    is_active boolean NOT NULL
  );

  INSERT INTO public.job_posting (
    id,
    company_id,
    titles,
    source_url,
    first_seen_at,
    is_active
  ) VALUES
    (
      '20000000-0000-4000-8000-000000000001',
      '10000000-0000-4000-8000-000000000001',
      ARRAY['Principal Fixture Engineer'],
      'https://fixture.invalid/jobs/one',
      '2026-07-01T09:30:00Z',
      true
    ),
    (
      '20000000-0000-4000-8000-000000000002',
      '10000000-0000-4000-8000-000000000002',
      ARRAY['Fixture Reliability Engineer'],
      'https://fixture.invalid/jobs/two',
      '2026-07-02T09:30:00Z',
      false
    );
`;

async function resetDatabase(sql: Sql): Promise<void> {
  await sql.unsafe("DROP SCHEMA IF EXISTS retirement_fixture_legacy CASCADE");
  await sql.unsafe("DROP SCHEMA IF EXISTS drizzle CASCADE");
  await sql.unsafe("DROP SCHEMA IF EXISTS public CASCADE");
  await sql.unsafe("CREATE SCHEMA public");
}

async function buildFixture(
  sql: Sql,
  ledgerSeed: MigrationMeta[],
  includeJobPosting: boolean,
): Promise<void> {
  await resetDatabase(sql);
  await sql.unsafe(baseFixtureSql);
  if (includeJobPosting) await sql.unsafe(jobPostingFixtureSql);

  for (const migration of ledgerSeed) {
    await sql`
      INSERT INTO drizzle.__drizzle_migrations (hash, created_at)
      VALUES (${migration.hash}, ${migration.folderMillis})
    `;
  }
}

function attestationEnvironment(mode: AttestationMode): Record<string, string> {
  const production = mode === "production-drop";
  const confirmation = production ? productionConfirmation : restoreConfirmation;
  const ids = production ? [101, 202, 303] : [0, 0, 0];
  const canonical = [
    mode,
    confirmation,
    String(ids[0]),
    String(ids[1]),
    String(ids[2]),
    fixtureWebSha,
  ].join("\n");

  return {
    RETIREMENT_ATTESTATION_MODE: mode,
    RETIREMENT_CONFIRMATION: confirmation,
    RETIREMENT_BACKUP_RESTORE_RUN_ID: String(ids[0]),
    RETIREMENT_CRAWLER_DEPLOY_RUN_ID: String(ids[1]),
    RETIREMENT_TYPESENSE_BACKFILL_RUN_ID: String(ids[2]),
    RETIREMENT_WEB_DEPLOY_SHA: fixtureWebSha,
    RETIREMENT_READINESS_DIGEST: createHash("sha256")
      .update(canonical)
      .digest("hex"),
  };
}

async function invokeRealMigration(
  databaseUrl: string,
  mode?: AttestationMode,
): Promise<MigrationResult> {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    DATABASE_URL_UNPOOLED: databaseUrl,
    MIGRATION_REQUIRE_UNPOOLED: "true",
    // Empty values prevent dotenv from silently supplying an attestation in
    // the ordinary-CLI negative case.
    RETIREMENT_ATTESTATION_MODE: "",
    RETIREMENT_CONFIRMATION: "",
    RETIREMENT_BACKUP_RESTORE_RUN_ID: "",
    RETIREMENT_CRAWLER_DEPLOY_RUN_ID: "",
    RETIREMENT_TYPESENSE_BACKFILL_RUN_ID: "",
    RETIREMENT_WEB_DEPLOY_SHA: "",
    RETIREMENT_READINESS_DIGEST: "",
  };
  if (mode) Object.assign(env, attestationEnvironment(mode));

  return await new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, [tsxRunner, migrationRunner], {
      cwd: webRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      output += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      output += chunk;
    });
    child.once("error", reject);
    child.once("close", (code) => resolvePromise({ code, output }));
  });
}

async function captureProof(sql: Sql): Promise<FixtureProof> {
  const publicTables = await sql<{ table_name: string }[]>`
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name
  `;
  const ledgerRows = await sql<
    { id: number; hash: string; created_at: string }[]
  >`
    SELECT id, hash, created_at
    FROM drizzle.__drizzle_migrations
    ORDER BY id
  `;
  const savedRows = await sql`
    SELECT
      id::text,
      user_id,
      job_posting_id::text,
      saved_at::text,
      posting_title,
      posting_source_url,
      posting_first_seen_at::text,
      posting_is_active,
      posting_salary_min,
      posting_salary_max,
      posting_salary_currency,
      posting_salary_period,
      company_id::text,
      company_name,
      company_slug,
      company_icon
    FROM public.saved_job
    ORDER BY id
  `;
  const relationshipDefinitions = await sql`
    SELECT object_type, object_name, definition
    FROM (
      SELECT
        'constraint'::text AS object_type,
        constraint_row.conname AS object_name,
        pg_get_constraintdef(constraint_row.oid, true) AS definition
      FROM pg_constraint AS constraint_row
      WHERE constraint_row.conname IN (
        'saved_job_user_id_user_id_fk',
        'saved_job_snapshot_text_nonblank_check',
        'application_interview_saved_job_id_fkey'
      )
      UNION ALL
      SELECT
        'index'::text,
        index_row.relname,
        pg_get_indexdef(index_row.oid)
      FROM pg_class AS index_row
      WHERE index_row.oid = to_regclass('public.idx_sj_user_posting')
    ) AS durable_relationships
    ORDER BY object_type, object_name
  `;
  const [linked] = await sql<{ count: string }[]>`
    SELECT count(*)::text AS count
    FROM public.application_interview AS interview
    JOIN public.saved_job AS saved ON saved.id = interview.saved_job_id
    JOIN public."user" AS account_user ON account_user.id = saved.user_id
  `;
  const guardObjects = await sql`
    SELECT kind, name, definition
    FROM (
      SELECT
        'foreign_key'::text AS kind,
        constraint_row.conname AS name,
        pg_get_constraintdef(constraint_row.oid, true) AS definition
      FROM pg_constraint AS constraint_row
      WHERE constraint_row.contype = 'f'
        AND constraint_row.confrelid = to_regclass('public.job_posting')
      UNION ALL
      SELECT
        'routine'::text,
        routine.proname,
        pg_get_functiondef(routine.oid)
      FROM pg_proc AS routine
      JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
      WHERE namespace.nspname <> 'information_schema'
        AND namespace.nspname !~ '^pg_'
        AND routine.prokind IN ('f', 'p')
        AND pg_get_functiondef(routine.oid) ~* '\\mjob_posting\\M'
    ) AS retirement_guards
    ORDER BY kind, name
  `;
  const [relation] = await sql<{ present: boolean }[]>`
    SELECT to_regclass('public.job_posting') IS NOT NULL AS present
  `;

  return {
    publicTables: publicTables.map((row) => row.table_name),
    ledger: ledgerRows.map((row) => ({
      id: Number(row.id),
      hash: row.hash,
      createdAt: String(row.created_at),
    })),
    savedJobDigest: sha256(savedRows),
    relationshipDigest: sha256(relationshipDefinitions),
    linkedRowCount: Number(linked?.count ?? -1),
    guardObjectDigest: sha256(guardObjects),
    guardObjectCount: guardObjects.length,
    jobPostingPresent: relation?.present === true,
  };
}

function expectedLedger(
  seed: MigrationMeta[],
  applied: MigrationMeta[] = [],
): Array<{ hash: string; createdAt: string }> {
  return [...seed, ...applied].map((migration) => ({
    hash: migration.hash,
    createdAt: String(migration.folderMillis),
  }));
}

function assertLedger(
  proof: FixtureProof,
  seed: MigrationMeta[],
  applied: MigrationMeta[] = [],
): void {
  assertEqual(
    proof.ledger.map(({ hash, createdAt }) => ({ hash, createdAt })),
    expectedLedger(seed, applied),
    "Drizzle ledger does not exactly match the real journal/hash fixture",
  );
  assertEqual(
    proof.ledger.map((row) => row.id),
    Array.from({ length: proof.ledger.length }, (_, index) => index + 1),
    "Drizzle ledger IDs are not contiguous",
  );
}

async function expectAtomicFailure(
  sql: Sql,
  databaseUrl: string,
  label: string,
  mode?: AttestationMode,
  expectedGuardObjectCount?: number,
): Promise<void> {
  const before = await captureProof(sql);
  if (expectedGuardObjectCount !== undefined) {
    invariant(
      before.guardObjectCount === expectedGuardObjectCount,
      `${label}: fixture catalog guard count is ${before.guardObjectCount}, expected ${expectedGuardObjectCount}`,
    );
  }
  const result = await invokeRealMigration(databaseUrl, mode);
  invariant(result.code !== 0, `${label}: real migration runner unexpectedly succeeded`);
  const after = await captureProof(sql);
  assertEqual(after, before, `${label}: failed migration was not atomic`);
  console.log(`PASS ${label} (atomic failure)`);
}

async function runHarness(databaseUrl: string): Promise<void> {
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: { application_name: "jobseek-retirement-pg17-fixture" },
  });

  try {
    const [server] = await sql<{ version: number; database_name: string }[]>`
      SELECT
        current_setting('server_version_num')::integer AS version,
        current_database() AS database_name
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
      server.database_name === requestedDatabase,
      "Connected database does not match the explicitly supplied URL",
    );

    const { seed, retirement, subsequent } = await loadLedgerFixture();

    await buildFixture(sql, seed, true);
    const beforeSuccess = await captureProof(sql);
    invariant(beforeSuccess.jobPostingPresent, "Production fixture lacks public.job_posting");
    invariant(beforeSuccess.linkedRowCount === 2, "Durable relationship fixture is incomplete");
    assertLedger(beforeSuccess, seed);
    const production = await invokeRealMigration(databaseUrl, "production-drop");
    invariant(
      production.code === 0,
      `Attested production fixture failed through src/db/migrate.ts:\n${production.output}`,
    );
    const afterSuccess = await captureProof(sql);
    invariant(!afterSuccess.jobPostingPresent, "0086 did not remove public.job_posting");
    assertEqual(
      afterSuccess.publicTables,
      [
        ...beforeSuccess.publicTables.filter((table) => table !== "job_posting"),
        "notification_delivery",
      ].sort(),
      "0086 plus subsequent additive migrations changed unexpected public tables",
    );
    invariant(
      afterSuccess.savedJobDigest === beforeSuccess.savedJobDigest,
      "0086 changed the saved_job row digest",
    );
    invariant(
      afterSuccess.relationshipDigest === beforeSuccess.relationshipDigest &&
        afterSuccess.linkedRowCount === beforeSuccess.linkedRowCount,
      "0086 changed saved-job user/interview relationships",
    );
    assertLedger(afterSuccess, seed, [retirement, ...subsequent]);
    console.log("PASS attested production drop preserves rows/relationships and exact ledger");

    await buildFixture(sql, seed, true);
    await sql.unsafe(`
      CREATE TABLE public.retirement_fixture_inbound_fk (
        id integer PRIMARY KEY,
        job_posting_id uuid NOT NULL,
        CONSTRAINT retirement_fixture_inbound_fk_job_posting_fkey
          FOREIGN KEY (job_posting_id) REFERENCES public.job_posting(id)
      )
    `);
    await expectAtomicFailure(
      sql,
      databaseUrl,
      "inbound job_posting foreign key",
      "production-drop",
      1,
    );

    await buildFixture(sql, seed, true);
    await sql.unsafe(`
      CREATE FUNCTION public.legacy_mirror_count()
      RETURNS bigint
      LANGUAGE sql
      AS 'SELECT count(*)::bigint FROM public.job_posting'
    `);
    await expectAtomicFailure(
      sql,
      databaseUrl,
      "public routine text reference",
      "production-drop",
      1,
    );

    await buildFixture(sql, seed, true);
    await sql.unsafe(`
      CREATE SCHEMA retirement_fixture_legacy;
      CREATE FUNCTION retirement_fixture_legacy.legacy_mirror_count()
      RETURNS bigint
      LANGUAGE sql
      AS 'SELECT count(*)::bigint FROM public.job_posting'
    `);
    await expectAtomicFailure(
      sql,
      databaseUrl,
      "non-public application routine text reference",
      "production-drop",
      1,
    );

    await buildFixture(sql, seed, true);
    await expectAtomicFailure(
      sql,
      databaseUrl,
      "ordinary migration CLI without TEMP attestation",
    );

    await buildFixture(sql, seed, false);
    const restoreShape = await captureProof(sql);
    invariant(!restoreShape.jobPostingPresent, "Restore fixture unexpectedly has job_posting");
    assertLedger(restoreShape, seed);
    await expectAtomicFailure(
      sql,
      databaseUrl,
      "restore-only shape without TEMP attestation",
    );
    await expectAtomicFailure(
      sql,
      databaseUrl,
      "restore-only shape under production-drop mode",
      "production-drop",
    );
    const restore = await invokeRealMigration(databaseUrl, "restore-drill");
    invariant(
      restore.code === 0,
      `Restore-drill convergence failed through src/db/migrate.ts:\n${restore.output}`,
    );
    const restored = await captureProof(sql);
    invariant(!restored.jobPostingPresent, "Restore convergence recreated job_posting");
    assertEqual(
      restored.publicTables,
      [...restoreShape.publicTables, "notification_delivery"].sort(),
      "Restore convergence changed unexpected public tables",
    );
    invariant(
      restored.savedJobDigest === restoreShape.savedJobDigest &&
        restored.relationshipDigest === restoreShape.relationshipDigest &&
        restored.linkedRowCount === restoreShape.linkedRowCount,
      "Restore convergence changed saved-job rows or relationships",
    );
    assertLedger(restored, seed, [retirement, ...subsequent]);
    console.log("PASS absent-source fixture converges only in restore-drill mode");
  } finally {
    try {
      await resetDatabase(sql);
    } finally {
      await sql.end({ timeout: 5 });
    }
  }
}

async function main(): Promise<void> {
  const databaseUrl = parseDatabaseUrl(process.argv.slice(2));
  await runHarness(databaseUrl);
  console.log("PostgreSQL 17 job_posting retirement execution harness passed.");
}

void main().catch((error: unknown) => {
  const message =
    error instanceof Error ? error.message : "Unknown retirement harness failure";
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
