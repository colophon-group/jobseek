import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { readMigrationFiles, type MigrationMeta } from "drizzle-orm/migrator";
import postgres, { type Sql } from "postgres";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const migrationFolder = resolve(webRoot, "drizzle");
const migrationScript = resolve(
  webRoot,
  "scripts/apply-better-auth-account-issuer.ts",
);
const tsxRunner = resolve(webRoot, "node_modules/tsx/dist/cli.mjs");

const prerequisiteTag = "0086_drop_supabase_job_posting";
const targetTag = "0087_better_auth_account_issuer";
const prerequisiteCreatedAt = 1_785_760_800_000;
const targetCreatedAt = 1_787_560_116_000;
const identityIndexName = "account_issuer_account_id_uidx";
const compatibilityTriggerName = "account_issuer_compat_before_write";
const compatibilityFunctionName = "jobseek_better_auth_account_issuer_compat";

const expectedIssuers = [
  ["credential", "local:credential"],
  ["github", "local:oauth:github"],
  ["google", "https://accounts.google.com"],
  ["linkedin", "local:oauth:linkedin"],
] as const;

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

interface LegacyAccountRow {
  id: string;
  accountId: string;
  providerId: string;
  userId: string;
  accessToken: string | null;
  refreshToken: string | null;
  idToken: string | null;
  accessTokenExpiresAt: string | null;
  refreshTokenExpiresAt: string | null;
  scope: string | null;
  password: string | null;
  createdAt: string;
  updatedAt: string;
}

interface IssuerRow {
  id: string;
  providerId: string;
  issuer: string;
}

interface IndexRow {
  name: string;
  isUnique: boolean;
  isValid: boolean;
  isReady: boolean;
  predicate: string | null;
  columns: string[];
  definition: string;
}

interface TriggerRow {
  triggerName: string;
  triggerEnabled: string;
  functionSchema: string;
  functionName: string;
  triggerDefinition: string;
  functionDefinition: string;
}

interface AtomicProof {
  ledger: LedgerRow[];
  columns: unknown[];
  indexes: unknown[];
  triggers: unknown[];
  functions: unknown[];
  users: unknown[];
  accounts: unknown[];
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

function normalizeSql(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function parseDatabaseUrl(argv: string[]): string {
  const flagIndex = argv.indexOf("--database-url");
  if (flagIndex === -1 || !argv[flagIndex + 1]) {
    throw new Error(
      "Usage: tsx scripts/test-better-auth-account-issuer-pg17.ts --database-url <disposable-postgresql-url>",
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
  if (!/(?:issuer|fixture)/i.test(databaseName)) {
    throw new Error(
      "Refusing destructive fixture setup: disposable database name must contain 'issuer' or 'fixture'",
    );
  }

  return rawUrl;
}

async function loadLedgerFixture(): Promise<{
  seed: MigrationMeta[];
  target: MigrationMeta;
}> {
  const journal = JSON.parse(
    await readFile(resolve(migrationFolder, "meta/_journal.json"), "utf8"),
  ) as Journal;
  const migrations = readMigrationFiles({ migrationsFolder: migrationFolder });

  invariant(journal.entries.length === migrations.length, "Journal and SQL migration counts differ");
  invariant(migrations.length === 77, `Expected 77 real journal migrations, found ${migrations.length}`);

  const prerequisiteIndex = journal.entries.findIndex(
    (entry) => entry.tag === prerequisiteTag,
  );
  const targetIndex = journal.entries.findIndex((entry) => entry.tag === targetTag);
  invariant(prerequisiteIndex === 74, "Expected 0086 at journal index 74");
  invariant(targetIndex === 75, "Expected 0087 at journal index 75");

  const prerequisite = migrations[prerequisiteIndex];
  const target = migrations[targetIndex];
  invariant(prerequisite, "0086 migration metadata is absent");
  invariant(target, "0087 migration metadata is absent");
  invariant(
    prerequisite.folderMillis === prerequisiteCreatedAt,
    "0086 migration timestamp differs",
  );
  invariant(target.folderMillis === targetCreatedAt, "0087 migration timestamp differs");

  // Production reached 0086 with one historical duplicate of the oldest real
  // migration, so its post-0086 ledger has 76 rows even though the journal has
  // 75 entries through 0086. Reproduce that exact real-hash/timestamp shape.
  const through0086 = migrations.slice(0, targetIndex);
  const seed = [through0086[0], ...through0086];
  invariant(seed.length === 76, "Post-0086 ledger fixture must contain exactly 76 rows");
  invariant(seed.every(Boolean), "Ledger fixture contains an absent migration");
  invariant(seed.at(-1)?.hash === prerequisite.hash, "Ledger fixture does not end at 0086");

  return { seed, target };
}

const fixtureSql = String.raw`
  CREATE SCHEMA drizzle;
  CREATE TABLE drizzle.__drizzle_migrations (
    id serial PRIMARY KEY,
    hash text NOT NULL,
    created_at bigint
  );

  CREATE TABLE public."user" (
    id text PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL UNIQUE,
    email_verified boolean NOT NULL DEFAULT false,
    image text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now()
  );

  CREATE TABLE public.account (
    id text PRIMARY KEY,
    account_id text NOT NULL,
    provider_id text NOT NULL,
    user_id text NOT NULL,
    access_token text,
    refresh_token text,
    id_token text,
    access_token_expires_at timestamp with time zone,
    refresh_token_expires_at timestamp with time zone,
    scope text,
    password text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT account_user_id_user_id_fk
      FOREIGN KEY (user_id) REFERENCES public."user"(id)
      ON DELETE CASCADE ON UPDATE NO ACTION
  );

  CREATE INDEX account_user_id_idx ON public.account (user_id);

  INSERT INTO public."user" (id, name, email, email_verified) VALUES
    ('user-credential', 'Credential User', 'credential@fixture.invalid', true),
    ('user-github', 'GitHub User', 'github@fixture.invalid', true),
    ('user-google', 'Google User', 'google@fixture.invalid', true),
    ('user-linkedin', 'LinkedIn User', 'linkedin@fixture.invalid', true);

  INSERT INTO public.account (
    id,
    account_id,
    provider_id,
    user_id,
    access_token,
    refresh_token,
    id_token,
    access_token_expires_at,
    refresh_token_expires_at,
    scope,
    password,
    created_at,
    updated_at
  ) VALUES
    (
      'account-credential',
      'user-credential',
      'credential',
      'user-credential',
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      'fixture-password-hash',
      '2026-08-01T10:00:00Z',
      '2026-08-11T10:00:00Z'
    ),
    (
      'account-github',
      'github-1001',
      'github',
      'user-github',
      'github-access',
      'github-refresh',
      'github-id',
      '2026-09-01T10:00:00Z',
      '2026-10-01T10:00:00Z',
      'read:user user:email',
      NULL,
      '2026-08-02T10:00:00Z',
      '2026-08-12T10:00:00Z'
    ),
    (
      'account-google',
      'google-2002',
      'google',
      'user-google',
      'google-access',
      'google-refresh',
      'google-id',
      '2026-09-02T10:00:00Z',
      '2026-10-02T10:00:00Z',
      'openid email profile',
      NULL,
      '2026-08-03T10:00:00Z',
      '2026-08-13T10:00:00Z'
    ),
    (
      'account-linkedin',
      'linkedin-3003',
      'linkedin',
      'user-linkedin',
      'linkedin-access',
      'linkedin-refresh',
      'linkedin-id',
      '2026-09-03T10:00:00Z',
      '2026-10-03T10:00:00Z',
      'openid email profile',
      NULL,
      '2026-08-04T10:00:00Z',
      '2026-08-14T10:00:00Z'
    );
`;

async function resetDatabase(sql: Sql): Promise<void> {
  await sql.unsafe("DROP SCHEMA IF EXISTS drizzle CASCADE");
  await sql.unsafe("DROP SCHEMA IF EXISTS public CASCADE");
  await sql.unsafe("CREATE SCHEMA public");
}

async function buildFixture(sql: Sql, ledgerSeed: MigrationMeta[]): Promise<void> {
  await resetDatabase(sql);
  await sql.unsafe(fixtureSql);

  for (const migration of ledgerSeed) {
    await sql`
      INSERT INTO drizzle.__drizzle_migrations (hash, created_at)
      VALUES (${migration.hash}, ${migration.folderMillis})
    `;
  }
}

async function invokeExactMigration(databaseUrl: string): Promise<MigrationResult> {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    DATABASE_URL: "",
    DATABASE_URL_UNPOOLED: databaseUrl,
    MIGRATION_REQUIRE_UNPOOLED: "true",
  };

  return await new Promise((resolvePromise, reject) => {
    const child = spawn(
      process.execPath,
      [tsxRunner, migrationScript, "--database-url-style", "env"],
      {
        cwd: webRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
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

async function captureLedger(sql: Sql): Promise<LedgerRow[]> {
  const rows = await sql<{ id: number; hash: string; createdAt: string }[]>`
    SELECT id, hash, created_at::text AS "createdAt"
    FROM drizzle.__drizzle_migrations
    ORDER BY id
  `;
  return rows.map((row) => ({
    id: Number(row.id),
    hash: row.hash,
    createdAt: String(row.createdAt),
  }));
}

async function captureLegacyAccounts(sql: Sql): Promise<LegacyAccountRow[]> {
  return await sql<LegacyAccountRow[]>`
    SELECT
      id,
      account_id AS "accountId",
      provider_id AS "providerId",
      user_id AS "userId",
      access_token AS "accessToken",
      refresh_token AS "refreshToken",
      id_token AS "idToken",
      access_token_expires_at::text AS "accessTokenExpiresAt",
      refresh_token_expires_at::text AS "refreshTokenExpiresAt",
      scope,
      password,
      created_at::text AS "createdAt",
      updated_at::text AS "updatedAt"
    FROM public.account
    ORDER BY id
  `;
}

async function captureAtomicProof(sql: Sql): Promise<AtomicProof> {
  const ledger = await captureLedger(sql);
  const columns = await sql`
    SELECT
      column_name,
      data_type,
      is_nullable,
      column_default,
      ordinal_position
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'account'
    ORDER BY ordinal_position
  `;
  const indexes = await sql`
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = 'public' AND tablename = 'account'
    ORDER BY indexname
  `;
  const triggers = await sql`
    SELECT
      trigger_row.tgname,
      trigger_row.tgenabled,
      pg_get_triggerdef(trigger_row.oid, true) AS definition
    FROM pg_trigger AS trigger_row
    WHERE trigger_row.tgrelid = 'public.account'::regclass
      AND NOT trigger_row.tgisinternal
    ORDER BY trigger_row.tgname
  `;
  const functions = await sql`
    SELECT
      namespace.nspname,
      routine.proname,
      pg_get_functiondef(routine.oid) AS definition
    FROM pg_proc AS routine
    JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
    WHERE namespace.nspname = 'public'
    ORDER BY routine.proname, routine.oid
  `;
  const users = await sql`
    SELECT to_jsonb(user_row) AS row
    FROM public."user" AS user_row
    ORDER BY id
  `;
  const accounts = await sql`
    SELECT to_jsonb(account_row) AS row
    FROM public.account AS account_row
    ORDER BY id
  `;

  return { ledger, columns, indexes, triggers, functions, users, accounts };
}

function expectedLedger(migrations: MigrationMeta[]): Array<{
  hash: string;
  createdAt: string;
}> {
  return migrations.map((migration) => ({
    hash: migration.hash,
    createdAt: String(migration.folderMillis),
  }));
}

function assertLedger(ledger: LedgerRow[], migrations: MigrationMeta[]): void {
  assertEqual(
    ledger.map(({ hash, createdAt }) => ({ hash, createdAt })),
    expectedLedger(migrations),
    "Drizzle ledger does not exactly match the real journal/hash fixture",
  );
  assertEqual(
    ledger.map((row) => row.id),
    Array.from({ length: ledger.length }, (_, index) => index + 1),
    "Drizzle ledger IDs are not contiguous",
  );
}

async function assertIssuerContract(sql: Sql): Promise<void> {
  const [issuerColumn] = await sql<
    { dataType: string; isNullable: string; columnDefault: string | null }[]
  >`
    SELECT
      data_type AS "dataType",
      is_nullable AS "isNullable",
      column_default AS "columnDefault"
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'account'
      AND column_name = 'issuer'
  `;
  invariant(issuerColumn, "0087 did not add public.account.issuer");
  assertEqual(
    issuerColumn,
    { dataType: "text", isNullable: "NO", columnDefault: null },
    "public.account.issuer does not have the exact NOT NULL text contract",
  );

  const indexes = await sql<IndexRow[]>`
    SELECT
      index_relation.relname AS name,
      index_row.indisunique AS "isUnique",
      index_row.indisvalid AS "isValid",
      index_row.indisready AS "isReady",
      pg_get_expr(index_row.indpred, index_row.indrelid) AS predicate,
      ARRAY(
        SELECT pg_get_indexdef(index_row.indexrelid, key_number, true)
        FROM generate_series(1, index_row.indnkeyatts) AS key_number
        ORDER BY key_number
      ) AS columns,
      pg_get_indexdef(index_row.indexrelid) AS definition
    FROM pg_index AS index_row
    JOIN pg_class AS table_relation ON table_relation.oid = index_row.indrelid
    JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_relation.relnamespace
    JOIN pg_class AS index_relation ON index_relation.oid = index_row.indexrelid
    WHERE table_namespace.nspname = 'public'
      AND table_relation.relname = 'account'
    ORDER BY index_relation.relname
  `;
  const identityIndexes = indexes.filter(
    (index) => JSON.stringify(index.columns) === JSON.stringify(["issuer", "account_id"]),
  );
  invariant(identityIndexes.length === 1, "Expected one exact issuer/account_id index");
  const identityIndex = identityIndexes[0];
  invariant(identityIndex, "Issuer identity index is absent");
  invariant(
    identityIndex.name === identityIndexName &&
      identityIndex.name === identityIndex.name.toLowerCase(),
    `Issuer identity index is not named exactly ${identityIndexName}`,
  );
  invariant(
    identityIndex.isUnique &&
      identityIndex.isValid &&
      identityIndex.isReady &&
      identityIndex.predicate === null,
    "Issuer identity index is not an unconditional ready/valid unique index",
  );
  assertEqual(
    normalizeSql(identityIndex.definition),
    `CREATE UNIQUE INDEX ${identityIndexName} ON public.account USING btree (issuer, account_id)`,
    "Issuer identity index definition differs",
  );

  const triggers = await sql<TriggerRow[]>`
    SELECT
      trigger_row.tgname AS "triggerName",
      trigger_row.tgenabled AS "triggerEnabled",
      function_namespace.nspname AS "functionSchema",
      routine.proname AS "functionName",
      pg_get_triggerdef(trigger_row.oid, true) AS "triggerDefinition",
      pg_get_functiondef(routine.oid) AS "functionDefinition"
    FROM pg_trigger AS trigger_row
    JOIN pg_proc AS routine ON routine.oid = trigger_row.tgfoid
    JOIN pg_namespace AS function_namespace ON function_namespace.oid = routine.pronamespace
    WHERE trigger_row.tgrelid = 'public.account'::regclass
      AND NOT trigger_row.tgisinternal
    ORDER BY trigger_row.tgname
  `;
  invariant(triggers.length === 1, "Expected exactly one account compatibility trigger");
  const trigger = triggers[0];
  invariant(trigger, "Account compatibility trigger is absent");
  invariant(
    trigger.triggerName === compatibilityTriggerName && trigger.triggerEnabled === "O",
    "Account compatibility trigger has the wrong name or is not enabled",
  );
  invariant(
    trigger.functionSchema === "public" && trigger.functionName === compatibilityFunctionName,
    "Account compatibility trigger calls the wrong function",
  );
  const triggerDefinition = normalizeSql(trigger.triggerDefinition);
  invariant(
    triggerDefinition.includes("BEFORE INSERT OR UPDATE") &&
      triggerDefinition.includes("provider_id") &&
      triggerDefinition.includes("issuer") &&
      /ON (?:public\.)?account/.test(triggerDefinition) &&
      triggerDefinition.includes("FOR EACH ROW"),
    "Account compatibility trigger definition differs",
  );
  const functionDefinition = normalizeSql(trigger.functionDefinition);
  for (const [provider, issuer] of expectedIssuers) {
    invariant(
      functionDefinition.includes(`WHEN '${provider}' THEN '${issuer}'`),
      `Compatibility function is missing the ${provider} issuer mapping`,
    );
  }
}

async function assertBackfilledMappings(sql: Sql, ids: string[]): Promise<void> {
  const rows = await sql<IssuerRow[]>`
    SELECT id, provider_id AS "providerId", issuer
    FROM public.account
    WHERE id = ANY(${ids})
    ORDER BY provider_id
  `;
  assertEqual(
    rows.map((row) => [row.providerId, row.issuer]),
    [...expectedIssuers]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([provider, issuer]) => [provider, issuer]),
    "Account issuer mappings differ",
  );
}

async function assertOldRuntimeInserts(sql: Sql): Promise<void> {
  await sql`
    INSERT INTO public."user" (id, name, email, email_verified) VALUES
      ('legacy-user-credential', 'Legacy Credential', 'legacy-credential@fixture.invalid', true),
      ('legacy-user-github', 'Legacy GitHub', 'legacy-github@fixture.invalid', true),
      ('legacy-user-google', 'Legacy Google', 'legacy-google@fixture.invalid', true),
      ('legacy-user-linkedin', 'Legacy LinkedIn', 'legacy-linkedin@fixture.invalid', true)
  `;
  await sql`
    INSERT INTO public.account (
      id,
      account_id,
      provider_id,
      user_id,
      access_token,
      password
    ) VALUES
      (
        'legacy-account-credential',
        'legacy-user-credential',
        'credential',
        'legacy-user-credential',
        NULL,
        'legacy-password-hash'
      ),
      (
        'legacy-account-github',
        'legacy-github-4004',
        'github',
        'legacy-user-github',
        'legacy-github-access',
        NULL
      ),
      (
        'legacy-account-google',
        'legacy-google-5005',
        'google',
        'legacy-user-google',
        'legacy-google-access',
        NULL
      ),
      (
        'legacy-account-linkedin',
        'legacy-linkedin-6006',
        'linkedin',
        'legacy-user-linkedin',
        'legacy-linkedin-access',
        NULL
      )
  `;

  await assertBackfilledMappings(sql, [
    "legacy-account-credential",
    "legacy-account-github",
    "legacy-account-google",
    "legacy-account-linkedin",
  ]);
  console.log("PASS Better Auth 1.6 inserts without issuer remain compatible");
}

async function expectCompatibilityWriteFailure(
  sql: Sql,
  label: string,
  expectedMessage: string,
  write: () => Promise<void>,
): Promise<void> {
  const before = await captureAtomicProof(sql);
  let errorMessage: string | null = null;

  try {
    await write();
  } catch (error: unknown) {
    errorMessage = error instanceof Error ? error.message : String(error);
  }

  invariant(errorMessage !== null, `${label}: compatibility trigger accepted the write`);
  invariant(
    errorMessage.includes(expectedMessage),
    `${label}: compatibility trigger returned an unexpected error: ${errorMessage}`,
  );
  assertEqual(
    await captureAtomicProof(sql),
    before,
    `${label}: rejected compatibility write changed database state`,
  );
  console.log(`PASS ${label} (compatibility trigger rejection)`);
}

async function expectAtomicFailure(
  sql: Sql,
  databaseUrl: string,
  label: string,
): Promise<void> {
  const before = await captureAtomicProof(sql);
  const result = await invokeExactMigration(databaseUrl);
  invariant(result.code !== 0, `${label}: exact issuer migration unexpectedly succeeded`);
  const after = await captureAtomicProof(sql);
  assertEqual(after, before, `${label}: failed issuer migration was not atomic`);
  console.log(`PASS ${label} (atomic failure)`);
}

async function runHarness(databaseUrl: string): Promise<void> {
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: { application_name: "jobseek-better-auth-issuer-pg17-fixture" },
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

    const { seed, target } = await loadLedgerFixture();

    await buildFixture(sql, seed);
    const legacyRows = await captureLegacyAccounts(sql);
    invariant(legacyRows.length === 4, "Success fixture does not contain four provider rows");
    assertLedger(await captureLedger(sql), seed);

    const migration = await invokeExactMigration(databaseUrl);
    invariant(
      migration.code === 0,
      `Exact Better Auth issuer migration failed:\n${migration.output}`,
    );
    invariant(migration.output.includes('"outcome":"applied"'), "Migration did not report applied");

    assertLedger(await captureLedger(sql), [...seed, target]);
    assertEqual(
      await captureLegacyAccounts(sql),
      legacyRows,
      "0087 changed a pre-existing account field other than issuer",
    );
    await assertBackfilledMappings(sql, [
      "account-credential",
      "account-github",
      "account-google",
      "account-linkedin",
    ]);
    await assertIssuerContract(sql);
    console.log("PASS exact post-0086 fixture migrates with preserved rows and issuer contract");

    const afterFirstApply = await captureAtomicProof(sql);
    const idempotent = await invokeExactMigration(databaseUrl);
    invariant(
      idempotent.code === 0,
      `Idempotent issuer migration failed:\n${idempotent.output}`,
    );
    invariant(
      idempotent.output.includes('"outcome":"already-applied"'),
      "Second migration did not report already-applied",
    );
    assertEqual(
      await captureAtomicProof(sql),
      afterFirstApply,
      "Idempotent issuer migration changed rows, ledger, or catalog objects",
    );
    console.log("PASS exact issuer migration is idempotent");

    await assertOldRuntimeInserts(sql);
    await expectCompatibilityWriteFailure(
      sql,
      "unsupported provider write",
      "unsupported provider_id enterprise-saml",
      async () => {
        await sql`
          INSERT INTO public.account (id, account_id, provider_id, user_id)
          VALUES ('rejected-account-unsupported', 'unsupported-7007', 'enterprise-saml', 'user-google')
        `;
      },
    );
    await expectCompatibilityWriteFailure(
      sql,
      "nonblank issuer/provider mismatch",
      "issuer does not match provider_id google",
      async () => {
        await sql`
          INSERT INTO public.account (id, account_id, provider_id, user_id, issuer)
          VALUES (
            'rejected-account-issuer-mismatch',
            'issuer-mismatch-8008',
            'google',
            'user-google',
            'local:oauth:github'
          )
        `;
      },
    );

    await buildFixture(sql, seed);
    await sql`
      INSERT INTO public."user" (id, name, email)
      VALUES ('user-unsupported', 'Unsupported Provider', 'unsupported@fixture.invalid')
    `;
    await sql`
      INSERT INTO public.account (id, account_id, provider_id, user_id)
      VALUES ('account-unsupported', 'unsupported-7007', 'enterprise-saml', 'user-unsupported')
    `;
    await expectAtomicFailure(sql, databaseUrl, "unsupported provider");

    await buildFixture(sql, seed);
    await sql`
      INSERT INTO public."user" (id, name, email)
      VALUES ('user-credential-mismatch', 'Credential Mismatch', 'mismatch@fixture.invalid')
    `;
    await sql`
      INSERT INTO public.account (id, account_id, provider_id, user_id, password)
      VALUES (
        'account-credential-mismatch',
        'different-account-id',
        'credential',
        'user-credential-mismatch',
        'fixture-password-hash'
      )
    `;
    await expectAtomicFailure(sql, databaseUrl, "credential identity mismatch");

    await buildFixture(sql, seed);
    await sql`
      INSERT INTO public."user" (id, name, email) VALUES
        ('user-collision-one', 'Collision One', 'collision-one@fixture.invalid'),
        ('user-collision-two', 'Collision Two', 'collision-two@fixture.invalid')
    `;
    await sql`
      INSERT INTO public.account (id, account_id, provider_id, user_id) VALUES
        ('account-collision-one', 'mapped-collision', 'google', 'user-collision-one'),
        ('account-collision-two', 'mapped-collision', 'google', 'user-collision-two')
    `;
    await expectAtomicFailure(sql, databaseUrl, "mapped issuer/account_id collision");
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
  console.log("PostgreSQL 17 Better Auth account issuer execution harness passed.");
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : "Unknown account issuer harness failure";
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
