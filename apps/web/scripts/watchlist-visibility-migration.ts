import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import dotenv from "dotenv";
import { readMigrationFiles, type MigrationMeta } from "drizzle-orm/migrator";
import postgres, { type Sql } from "postgres";

import { logExternalError } from "../src/lib/safe-external-error";

dotenv.config({ path: ".env.local", quiet: true });

type Command = "inventory" | "apply" | "verify" | "rollback";

type PathVariant = {
  locale: "en" | "de" | "fr" | "it";
  ownerSlugKind: "username" | "display_username";
  ownerSlug: string;
  pagePath: string;
  ogPath: string;
};

type InventoryRow = {
  watchlistId: string;
  ownerUserId: string;
  ownerName: string;
  ownerUsername: string | null;
  ownerDisplayUsername: string | null;
  watchlistSlug: string;
  watchlistPayload: Record<string, unknown>;
  companyMemberships: Array<Record<string, unknown>>;
  pathVariants: PathVariant[];
};

type AggregateEvidence = {
  watchlistCount: number;
  membershipCount: number;
  publicCount: number;
  privateCount: number;
  alertsEnabledCount: number;
  copiedWatchlistCount: number;
  watchlistContentDigest: string;
  filtersDigest: string;
  alertsDigest: string;
  provenanceDigest: string;
  ownersDigest: string;
  membershipDigest: string;
  publicInventoryDigest: string;
};

type InventoryArtifact = {
  formatVersion: 1;
  migrationTag: typeof targetTag;
  capturedAt: string;
  databaseName: string;
  serverVersion: string;
  prerequisite: {
    tag: typeof prerequisiteTag;
    createdAt: number;
    hash: string;
  };
  target: {
    tag: typeof targetTag;
    createdAt: number;
    hash: string;
  };
  aggregates: AggregateEvidence;
  publicWatchlists: InventoryRow[];
};

type Journal = {
  entries: Array<{ idx: number; when: number; tag: string }>;
};

const migrationFolder = resolve(process.cwd(), "drizzle");
const prerequisiteTag = "0088_notification_policy_foundation";
const targetTag = "0089_make_existing_watchlists_private";
const prerequisiteCreatedAt = 1_788_199_156_000;
const targetCreatedAt = 1_788_206_680_000;
const migrationKey = targetTag;

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function isCommand(value: string | undefined): value is Command {
  return (
    value === "inventory" ||
    value === "apply" ||
    value === "verify" ||
    value === "rollback"
  );
}

function requireDirectDatabaseUrl(command: Command): string {
  const databaseUrl = process.env.DATABASE_URL_UNPOOLED;
  invariant(
    databaseUrl,
    `DATABASE_URL_UNPOOLED must be set for watchlist visibility ${command}`,
  );
  invariant(
    new URL(databaseUrl).port !== "6543",
    "Refusing watchlist visibility work through the transaction pooler",
  );
  if (command === "apply" || command === "rollback") {
    invariant(
      process.env.MIGRATION_REQUIRE_UNPOOLED === "true",
      "MIGRATION_REQUIRE_UNPOOLED=true is required for writes",
    );
  }
  return databaseUrl;
}

function migrationByTag(
  journal: Journal,
  migrations: MigrationMeta[],
  tag: string,
): MigrationMeta {
  const index = journal.entries.findIndex((entry) => entry.tag === tag);
  invariant(index !== -1, `Migration journal does not contain ${tag}`);
  const migration = migrations[index];
  invariant(migration, `Migration metadata does not contain ${tag}`);
  invariant(
    migration.folderMillis === journal.entries[index]?.when,
    `Migration timestamp does not match journal entry ${tag}`,
  );
  return migration;
}

function loadMigrations(): {
  prerequisite: MigrationMeta;
  target: MigrationMeta;
} {
  const journal = JSON.parse(
    readFileSync(resolve(migrationFolder, "meta/_journal.json"), "utf8"),
  ) as Journal;
  const migrations = readMigrationFiles({ migrationsFolder: migrationFolder });
  invariant(
    journal.entries.length === migrations.length,
    "Journal and SQL migration counts differ",
  );

  const prerequisite = migrationByTag(
    journal,
    migrations,
    prerequisiteTag,
  );
  const target = migrationByTag(journal, migrations, targetTag);
  invariant(
    prerequisite.folderMillis === prerequisiteCreatedAt,
    "0088 prerequisite timestamp differs",
  );
  invariant(
    target.folderMillis === targetCreatedAt,
    "0089 target timestamp differs",
  );
  return { prerequisite, target };
}

function readArtifact(path: string): InventoryArtifact {
  const artifact = JSON.parse(readFileSync(resolve(path), "utf8")) as Partial<InventoryArtifact>;
  invariant(artifact.formatVersion === 1, "Unsupported inventory formatVersion");
  invariant(artifact.migrationTag === targetTag, "Inventory is for a different migration");
  invariant(artifact.prerequisite?.tag === prerequisiteTag, "Inventory prerequisite differs");
  invariant(artifact.target?.tag === targetTag, "Inventory target differs");
  invariant(artifact.aggregates, "Inventory aggregate evidence is absent");
  invariant(Array.isArray(artifact.publicWatchlists), "Inventory rows are absent");
  invariant(
    artifact.aggregates.publicCount === artifact.publicWatchlists.length,
    "Inventory row count differs from its public count",
  );
  invariant(
    /^[0-9a-f]{32}$/.test(artifact.aggregates.publicInventoryDigest),
    "Inventory digest is invalid",
  );
  return artifact as InventoryArtifact;
}

function writePrivateJson(path: string, value: unknown, exclusive: boolean): void {
  writeFileSync(resolve(path), `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: exclusive ? "wx" : "w",
  });
}

async function readAggregateEvidence(sql: Sql): Promise<AggregateEvidence> {
  const [watchlists] = await sql<
    Omit<AggregateEvidence, "membershipCount" | "membershipDigest" | "publicInventoryDigest">[]
  >`
    SELECT
      count(*)::integer AS "watchlistCount",
      count(*) FILTER (WHERE is_public)::integer AS "publicCount",
      count(*) FILTER (WHERE NOT is_public)::integer AS "privateCount",
      count(*) FILTER (WHERE alerts_enabled)::integer AS "alertsEnabledCount",
      count(*) FILTER (WHERE source_watchlist_id IS NOT NULL)::integer AS "copiedWatchlistCount",
      md5(COALESCE(jsonb_agg(to_jsonb(w) - 'is_public' ORDER BY w.id)::text, '[]'))
        AS "watchlistContentDigest",
      md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'filters', w.filters) ORDER BY w.id)::text, '[]'))
        AS "filtersDigest",
      md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'alertsEnabled', w.alerts_enabled) ORDER BY w.id)::text, '[]'))
        AS "alertsDigest",
      md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'sourceWatchlistId', w.source_watchlist_id) ORDER BY w.id)::text, '[]'))
        AS "provenanceDigest",
      md5(COALESCE(jsonb_agg(jsonb_build_object('watchlistId', w.id, 'ownerUserId', w.user_id) ORDER BY w.id)::text, '[]'))
        AS "ownersDigest"
    FROM public.watchlist AS w
  `;
  const [memberships] = await sql<
    Pick<AggregateEvidence, "membershipCount" | "membershipDigest">[]
  >`
    SELECT
      count(*)::integer AS "membershipCount",
      md5(COALESCE(jsonb_agg(to_jsonb(wc) ORDER BY wc.id)::text, '[]'))
        AS "membershipDigest"
    FROM public.watchlist_company AS wc
  `;
  const [publicInventory] = await sql<
    Pick<AggregateEvidence, "publicInventoryDigest">[]
  >`
    SELECT md5(COALESCE(jsonb_agg(
      jsonb_build_object(
        'watchlist', inventory.watchlist_payload,
        'ownerUserId', inventory.owner_user_id,
        'ownerName', inventory.owner_name,
        'ownerUsername', inventory.owner_username,
        'ownerDisplayUsername', inventory.owner_display_username,
        'companies', inventory.company_memberships
      ) ORDER BY inventory.watchlist_id
    )::text, '[]')) AS "publicInventoryDigest"
    FROM (
      SELECT
        w.id AS watchlist_id,
        to_jsonb(w) AS watchlist_payload,
        u.id AS owner_user_id,
        u.name AS owner_name,
        u.username AS owner_username,
        u.display_username AS owner_display_username,
        COALESCE(
          (
            SELECT jsonb_agg(to_jsonb(wc) ORDER BY wc.id)
            FROM public.watchlist_company AS wc
            WHERE wc.watchlist_id = w.id
          ),
          '[]'::jsonb
        ) AS company_memberships
      FROM public.watchlist AS w
      JOIN public."user" AS u ON u.id = w.user_id
      WHERE w.is_public
      ORDER BY w.id
    ) AS inventory
  `;
  invariant(watchlists && memberships && publicInventory, "Could not aggregate watchlists");
  return { ...watchlists, ...memberships, ...publicInventory };
}

async function readPublicRows(sql: Sql): Promise<InventoryRow[]> {
  return sql<InventoryRow[]>`
    SELECT
      w.id AS "watchlistId",
      u.id AS "ownerUserId",
      u.name AS "ownerName",
      u.username AS "ownerUsername",
      u.display_username AS "ownerDisplayUsername",
      w.slug AS "watchlistSlug",
      to_jsonb(w) AS "watchlistPayload",
      COALESCE(
        (
          SELECT jsonb_agg(to_jsonb(wc) ORDER BY wc.id)
          FROM public.watchlist_company AS wc
          WHERE wc.watchlist_id = w.id
        ),
        '[]'::jsonb
      ) AS "companyMemberships",
      COALESCE(
        (
          SELECT jsonb_agg(
            jsonb_build_object(
              'locale', locale.value,
              'ownerSlugKind', owner_slug.kind,
              'ownerSlug', owner_slug.value,
              'pagePath', format('/%s/%s/%s', locale.value, owner_slug.value, w.slug),
              'ogPath', format('/og/watchlist/%s/%s/%s', locale.value, owner_slug.value, w.slug)
            )
            ORDER BY owner_slug.kind, locale.value
          )
          FROM (
            VALUES
              ('username'::text, u.username),
              ('display_username'::text, u.display_username)
          ) AS owner_slug(kind, value)
          CROSS JOIN (
            VALUES ('en'::text), ('de'::text), ('fr'::text), ('it'::text)
          ) AS locale(value)
          WHERE owner_slug.value IS NOT NULL
        ),
        '[]'::jsonb
      ) AS "pathVariants"
    FROM public.watchlist AS w
    JOIN public."user" AS u ON u.id = w.user_id
    WHERE w.is_public
    ORDER BY w.id
  `;
}

async function assertCompatibilitySchema(sql: Sql): Promise<void> {
  const [column] = await sql<
    { count: number; defaultValue: string | null }[]
  >`
    SELECT
      count(*)::integer AS count,
      min(pg_get_expr(default_value.adbin, default_value.adrelid)) AS "defaultValue"
    FROM pg_attribute AS attribute
    LEFT JOIN pg_attrdef AS default_value
      ON default_value.adrelid = attribute.attrelid
     AND default_value.adnum = attribute.attnum
    WHERE attribute.attrelid = 'public.watchlist'::regclass
      AND attribute.attname = 'is_public'
      AND attribute.atttypid = 'boolean'::regtype
      AND attribute.attnotnull
      AND NOT attribute.attisdropped
  `;
  const [index] = await sql<
    { definition: string | null; valid: boolean | null; ready: boolean | null }[]
  >`
    SELECT
      pg_get_indexdef(indexrelid) AS definition,
      indisvalid AS valid,
      indisready AS ready
    FROM pg_index
    WHERE indexrelid = to_regclass('public.idx_wl_public')
  `;
  invariant(
    column?.count === 1 && column.defaultValue === "false",
    `watchlist.is_public is not the expected retained private-default boolean: ${JSON.stringify(column)}`,
  );
  invariant(
    index?.valid === true &&
      index.ready === true &&
      index.definition?.includes(" ON public.watchlist ") &&
      index.definition.includes("(is_public)") &&
      index.definition.includes("WHERE (is_public = true)"),
    `idx_wl_public is not the expected retained partial index: ${JSON.stringify(index)}`,
  );
}

async function assertLedgerPreflight(
  sql: Sql,
  prerequisite: MigrationMeta,
  target: MigrationMeta,
): Promise<void> {
  const [ledger] = await sql<
    { rowCount: number; latestCreatedAt: string | null; latestHash: string | null }[]
  >`
    SELECT
      count(*)::integer AS "rowCount",
      (array_agg(created_at::text ORDER BY created_at DESC, id DESC))[1]
        AS "latestCreatedAt",
      (array_agg(hash ORDER BY created_at DESC, id DESC))[1]
        AS "latestHash"
    FROM drizzle.__drizzle_migrations
  `;
  const [rows] = await sql<
    { prerequisiteExact: number; targetExact: number }[]
  >`
    SELECT
      count(*) FILTER (
        WHERE created_at = ${prerequisite.folderMillis}
          AND hash = ${prerequisite.hash}
      )::integer AS "prerequisiteExact",
      count(*) FILTER (
        WHERE created_at = ${target.folderMillis}
          AND hash = ${target.hash}
      )::integer AS "targetExact"
    FROM drizzle.__drizzle_migrations
  `;
  invariant(ledger && rows, "Could not read the migration ledger");
  invariant(rows.targetExact === 0, "0089 is already recorded; use verify");
  invariant(
    rows.prerequisiteExact === 1 &&
      ledger.rowCount === 78 &&
      Number(ledger.latestCreatedAt) === prerequisite.folderMillis &&
      ledger.latestHash === prerequisite.hash,
    `Expected exact post-0088 ledger before 0089: ${JSON.stringify({ ledger, rows })}`,
  );
}

async function inventory(sql: Sql, outputPath: string): Promise<void> {
  const { prerequisite, target } = loadMigrations();
  const artifact = await sql.begin(async (tx) => {
    await tx`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`;
    await tx`SET LOCAL statement_timeout = '60s'`;
    await tx`SET LOCAL idle_in_transaction_session_timeout = '90s'`;
    await assertLedgerPreflight(tx, prerequisite, target);
    await assertCompatibilitySchema(tx);

    const [relations] = await tx<{ artifactRelations: number }[]>`
      SELECT count(*)::integer AS "artifactRelations"
      FROM pg_class
      WHERE oid IN (
        to_regclass('public.watchlist_visibility_0089_state'),
        to_regclass('public.watchlist_visibility_0089_rollback')
      )
    `;
    const [orphans] = await tx<{ count: number }[]>`
      SELECT count(*)::integer AS count
      FROM public.watchlist AS w
      LEFT JOIN public."user" AS u ON u.id = w.user_id
      WHERE u.id IS NULL
    `;
    invariant(relations?.artifactRelations === 0, "0089 artifact relations already exist");
    invariant(orphans?.count === 0, "A watchlist is not owner-readable");

    const [database] = await tx<{ databaseName: string; serverVersion: string }[]>`
      SELECT
        current_database() AS "databaseName",
        current_setting('server_version') AS "serverVersion"
    `;
    const aggregates = await readAggregateEvidence(tx);
    const publicWatchlists = await readPublicRows(tx);
    invariant(database, "Could not read database identity");
    invariant(
      aggregates.publicCount === publicWatchlists.length,
      "Public inventory lost an owner or row",
    );
    for (const row of publicWatchlists) {
      const ownerSlugs = new Set(
        [row.ownerUsername, row.ownerDisplayUsername].filter(
          (value): value is string => value !== null,
        ),
      );
      invariant(
        row.pathVariants.length === ownerSlugs.size * 4,
        `Localized path inventory is incomplete for watchlist ${row.watchlistId}`,
      );
    }

    return {
      formatVersion: 1,
      migrationTag: targetTag,
      capturedAt: new Date().toISOString(),
      ...database,
      prerequisite: {
        tag: prerequisiteTag,
        createdAt: prerequisite.folderMillis,
        hash: prerequisite.hash,
      },
      target: {
        tag: targetTag,
        createdAt: target.folderMillis,
        hash: target.hash,
      },
      aggregates,
      publicWatchlists,
    } satisfies InventoryArtifact;
  });

  writePrivateJson(outputPath, artifact, true);
  process.stdout.write(
    `${JSON.stringify({
      command: "inventory",
      output: resolve(outputPath),
      publicCount: artifact.aggregates.publicCount,
      publicInventoryDigest: artifact.aggregates.publicInventoryDigest,
      localizedPathCount: artifact.publicWatchlists.reduce(
        (count, row) => count + row.pathVariants.length,
        0,
      ),
    })}\n`,
  );
}

function requiredApplyEvidence(artifact: InventoryArtifact) {
  const confirmation = process.env.WATCHLIST_PRIVACY_CONFIRMATION;
  const rawBackupRunId = process.env.WATCHLIST_PRIVACY_BACKUP_RESTORE_RUN_ID;
  const privateMutationsDeploySha =
    process.env.WATCHLIST_PRIVATE_MUTATIONS_DEPLOY_SHA;
  const routeCutoverDeploySha = process.env.WATCHLIST_ROUTE_CUTOVER_DEPLOY_SHA;
  const routeCutoverApprovedBy =
    process.env.WATCHLIST_ROUTE_CUTOVER_APPROVED_BY;
  const backupRestoreRunId = Number(rawBackupRunId);

  invariant(confirmation === "PRIVATE-WATCHLISTS-0089", "Apply confirmation is invalid");
  invariant(
    rawBackupRunId && /^\d+$/.test(rawBackupRunId) && Number.isSafeInteger(backupRestoreRunId) && backupRestoreRunId > 0,
    "WATCHLIST_PRIVACY_BACKUP_RESTORE_RUN_ID must be a positive run ID",
  );
  invariant(
    privateMutationsDeploySha && /^[0-9a-f]{40}$/.test(privateMutationsDeploySha),
    "WATCHLIST_PRIVATE_MUTATIONS_DEPLOY_SHA must be a deployed 40-hex SHA",
  );
  invariant(
    routeCutoverDeploySha && /^[0-9a-f]{40}$/.test(routeCutoverDeploySha),
    "WATCHLIST_ROUTE_CUTOVER_DEPLOY_SHA must be a deployed 40-hex SHA",
  );
  invariant(
    routeCutoverApprovedBy?.trim(),
    "WATCHLIST_ROUTE_CUTOVER_APPROVED_BY must record the human approver",
  );

  return {
    confirmation,
    backupRestoreRunId,
    privateMutationsDeploySha,
    routeCutoverDeploySha,
    routeCutoverApprovedBy,
    expectedPublicCount: artifact.aggregates.publicCount,
    expectedPublicDigest: artifact.aggregates.publicInventoryDigest,
  };
}

async function applyMigration(sql: Sql, artifact: InventoryArtifact): Promise<void> {
  const { prerequisite, target } = loadMigrations();
  invariant(
    artifact.prerequisite.hash === prerequisite.hash &&
      artifact.target.hash === target.hash,
    "Inventory was not captured for the checked-out migration hashes",
  );
  const evidence = requiredApplyEvidence(artifact);

  const outcome = await sql.begin(async (tx) => {
    await tx`SET LOCAL lock_timeout = '10s'`;
    await tx`SET LOCAL statement_timeout = '10min'`;
    await tx`SET LOCAL idle_in_transaction_session_timeout = '2min'`;
    await tx`
      SELECT pg_advisory_xact_lock(
        hashtextextended('jobseek:web-schema-migrations', 0)
      )
    `;

    const [targetLedger] = await tx<{ count: number }[]>`
      SELECT count(*)::integer AS count
      FROM drizzle.__drizzle_migrations
      WHERE created_at = ${target.folderMillis}
        AND hash = ${target.hash}
    `;
    invariant(targetLedger, "Could not read the 0089 ledger row");
    if (targetLedger.count === 1) {
      const [state] = await tx<{ status: string; publicCount: number }[]>`
        SELECT
          state.status,
          count(*) FILTER (WHERE w.is_public)::integer AS "publicCount"
        FROM public.watchlist_visibility_0089_state AS state
        CROSS JOIN public.watchlist AS w
        WHERE state.migration_key = ${migrationKey}
        GROUP BY state.status
      `;
      invariant(
        state?.status === "private" && state.publicCount === 0,
        "0089 is recorded but the private postcondition does not hold",
      );
      return "already-applied" as const;
    }
    invariant(targetLedger.count === 0, "0089 is recorded more than once");
    await assertLedgerPreflight(tx, prerequisite, target);

    await tx`
      CREATE TEMPORARY TABLE jobseek_watchlist_privacy_attestation (
        confirmation text NOT NULL,
        backup_restore_run_id bigint NOT NULL,
        private_mutations_deploy_sha text NOT NULL,
        route_cutover_deploy_sha text NOT NULL,
        route_cutover_approved_by text NOT NULL,
        expected_public_count bigint NOT NULL,
        expected_public_digest text NOT NULL,
        attested_at timestamp with time zone NOT NULL
      ) ON COMMIT DROP
    `;
    await tx`
      INSERT INTO pg_temp.jobseek_watchlist_privacy_attestation (
        confirmation,
        backup_restore_run_id,
        private_mutations_deploy_sha,
        route_cutover_deploy_sha,
        route_cutover_approved_by,
        expected_public_count,
        expected_public_digest,
        attested_at
      ) VALUES (
        ${evidence.confirmation},
        ${evidence.backupRestoreRunId},
        ${evidence.privateMutationsDeploySha},
        ${evidence.routeCutoverDeploySha},
        ${evidence.routeCutoverApprovedBy},
        ${evidence.expectedPublicCount},
        ${evidence.expectedPublicDigest},
        clock_timestamp()
      )
    `;

    for (const statement of target.sql) {
      if (statement.trim()) await tx.unsafe(statement);
    }
    await tx`
      INSERT INTO drizzle.__drizzle_migrations (hash, created_at)
      VALUES (${target.hash}, ${target.folderMillis})
    `;
    return "applied" as const;
  });

  process.stdout.write(`${JSON.stringify({ command: "apply", migration: targetTag, outcome })}\n`);
}

async function readState(sql: Sql) {
  const [state] = await sql<
    ({ status: "private" | "rolled_back" } & Omit<
      AggregateEvidence,
      "privateCount" | "alertsEnabledCount" | "copiedWatchlistCount"
    >)[]
  >`
    SELECT
      status,
      watchlist_count::integer AS "watchlistCount",
      membership_count::integer AS "membershipCount",
      public_count::integer AS "publicCount",
      watchlist_content_digest AS "watchlistContentDigest",
      filters_digest AS "filtersDigest",
      alerts_digest AS "alertsDigest",
      provenance_digest AS "provenanceDigest",
      owners_digest AS "ownersDigest",
      membership_digest AS "membershipDigest",
      public_inventory_digest AS "publicInventoryDigest"
    FROM public.watchlist_visibility_0089_state
    WHERE migration_key = ${migrationKey}
  `;
  invariant(state, "0089 migration state is absent");
  return state;
}

async function assertRollbackArtifact(
  sql: Sql,
  artifact: InventoryArtifact,
): Promise<{ changedRows: number; changedMemberships: number; ownerFailures: number }> {
  const [evidence] = await sql<
    { rowCount: number; digest: string; changedRows: number; changedMemberships: number; ownerFailures: number }[]
  >`
    SELECT
      count(*)::integer AS "rowCount",
      md5(COALESCE(jsonb_agg(
        jsonb_build_object(
          'watchlist', rollback.watchlist_payload,
          'ownerUserId', rollback.owner_user_id,
          'ownerName', rollback.owner_name,
          'ownerUsername', rollback.owner_username,
          'ownerDisplayUsername', rollback.owner_display_username,
          'companies', rollback.company_memberships
        ) ORDER BY rollback.watchlist_id
      )::text, '[]')) AS digest,
      count(*) FILTER (
        WHERE w.id IS NULL
           OR (to_jsonb(w) - 'is_public') IS DISTINCT FROM
              (rollback.watchlist_payload - 'is_public')
      )::integer AS "changedRows",
      count(*) FILTER (
        WHERE rollback.company_memberships IS DISTINCT FROM COALESCE(
          (
            SELECT jsonb_agg(to_jsonb(wc) ORDER BY wc.id)
            FROM public.watchlist_company AS wc
            WHERE wc.watchlist_id = rollback.watchlist_id
          ),
          '[]'::jsonb
        )
      )::integer AS "changedMemberships",
      count(*) FILTER (
        WHERE u.id IS NULL OR w.user_id IS DISTINCT FROM rollback.owner_user_id
      )::integer AS "ownerFailures"
    FROM public.watchlist_visibility_0089_rollback AS rollback
    LEFT JOIN public.watchlist AS w ON w.id = rollback.watchlist_id
    LEFT JOIN public."user" AS u ON u.id = rollback.owner_user_id
  `;
  invariant(evidence, "Could not verify the rollback artifact");
  invariant(
    evidence.rowCount === artifact.aggregates.publicCount &&
      evidence.digest === artifact.aggregates.publicInventoryDigest,
    "Database rollback inventory differs from the reviewed artifact",
  );
  return evidence;
}

async function verify(
  sql: Sql,
  artifact: InventoryArtifact,
  outputPath: string | undefined,
): Promise<void> {
  const { target } = loadMigrations();
  const evidence = await sql.begin(async (tx) => {
    await tx`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`;
    await tx`SET LOCAL statement_timeout = '60s'`;
    await assertCompatibilitySchema(tx);
    const [ledger] = await tx<{ exact: number }[]>`
      SELECT count(*)::integer AS exact
      FROM drizzle.__drizzle_migrations
      WHERE created_at = ${target.folderMillis}
        AND hash = ${target.hash}
    `;
    invariant(ledger?.exact === 1, "The exact 0089 ledger row is not recorded once");
    const state = await readState(tx);
    const rollback = await assertRollbackArtifact(tx, artifact);
    const current = await readAggregateEvidence(tx);
    const currentWithoutVisibility = {
      watchlistCount: current.watchlistCount,
      membershipCount: current.membershipCount,
      watchlistContentDigest: current.watchlistContentDigest,
      filtersDigest: current.filtersDigest,
      alertsDigest: current.alertsDigest,
      provenanceDigest: current.provenanceDigest,
      ownersDigest: current.ownersDigest,
      membershipDigest: current.membershipDigest,
    };
    const stateWithoutVisibility = {
      watchlistCount: state.watchlistCount,
      membershipCount: state.membershipCount,
      watchlistContentDigest: state.watchlistContentDigest,
      filtersDigest: state.filtersDigest,
      alertsDigest: state.alertsDigest,
      provenanceDigest: state.provenanceDigest,
      ownersDigest: state.ownersDigest,
      membershipDigest: state.membershipDigest,
    };
    invariant(
      JSON.stringify(currentWithoutVisibility) === JSON.stringify(stateWithoutVisibility),
      "Postflight watchlist content differs from the transactional snapshot",
    );
    invariant(
      state.status === "private" && current.publicCount === 0,
      "Postflight visibility is not uniformly private",
    );
    invariant(
      state.publicCount === artifact.aggregates.publicCount &&
        state.publicInventoryDigest === artifact.aggregates.publicInventoryDigest,
      "Postflight state differs from the reviewed preflight inventory",
    );
    invariant(
      rollback.changedRows === 0 &&
        rollback.changedMemberships === 0 &&
        rollback.ownerFailures === 0,
      `Postflight rollback rows, memberships, or owner access changed: ${JSON.stringify(rollback)}`,
    );
    return {
      checkedAt: new Date().toISOString(),
      migration: targetTag,
      status: state.status,
      migratedPublicCount: state.publicCount,
      remainingPublicCount: current.publicCount,
      retainedWatchlistCount: current.watchlistCount,
      retainedMembershipCount: current.membershipCount,
      retainedAlertsEnabledCount: current.alertsEnabledCount,
      retainedCopiedWatchlistCount: current.copiedWatchlistCount,
      publicInventoryDigest: state.publicInventoryDigest,
      rollbackArtifactRows: artifact.publicWatchlists.length,
      rollback,
      retainedCompatibility: {
        isPublicColumn: true,
        publicPartialIndex: true,
      },
    };
  });
  if (outputPath) writePrivateJson(outputPath, evidence, false);
  process.stdout.write(`${JSON.stringify(evidence)}\n`);
}

async function rollback(sql: Sql, artifact: InventoryArtifact): Promise<void> {
  invariant(
    process.env.WATCHLIST_PRIVACY_ROLLBACK_CONFIRMATION ===
      "ROLLBACK-PRIVATE-WATCHLISTS-0089",
    "Rollback confirmation is invalid",
  );
  invariant(
    process.env.WATCHLIST_PRIVACY_ROLLBACK_OWNER?.trim(),
    "WATCHLIST_PRIVACY_ROLLBACK_OWNER must identify the responsible operator",
  );
  const { target } = loadMigrations();

  const outcome = await sql.begin(async (tx) => {
    await tx`SET LOCAL lock_timeout = '10s'`;
    await tx`SET LOCAL statement_timeout = '10min'`;
    await tx`SET LOCAL idle_in_transaction_session_timeout = '2min'`;
    await tx`
      SELECT pg_advisory_xact_lock(
        hashtextextended('jobseek:web-schema-migrations', 0)
      )
    `;
    await tx`LOCK TABLE public.watchlist IN SHARE ROW EXCLUSIVE MODE`;
    await tx`LOCK TABLE public.watchlist_company IN SHARE ROW EXCLUSIVE MODE`;
    await tx`LOCK TABLE public."user" IN SHARE MODE`;
    await assertCompatibilitySchema(tx);

    const [ledger] = await tx<{ exact: number }[]>`
      SELECT count(*)::integer AS exact
      FROM drizzle.__drizzle_migrations
      WHERE created_at = ${target.folderMillis}
        AND hash = ${target.hash}
    `;
    invariant(ledger?.exact === 1, "Cannot rollback without the exact 0089 ledger row");
    const state = await readState(tx);
    invariant(state.status === "private", `Cannot rollback from status ${state.status}`);
    const before = await assertRollbackArtifact(tx, artifact);
    invariant(
      before.changedRows === 0 &&
        before.changedMemberships === 0 &&
        before.ownerFailures === 0,
      `Rollback abort: protected rows changed after migration: ${JSON.stringify(before)}`,
    );
    const [visibility] = await tx<{ publicCount: number; inventoryPublicCount: number }[]>`
      SELECT
        count(*) FILTER (WHERE w.is_public)::integer AS "publicCount",
        count(*) FILTER (
          WHERE w.is_public AND rollback.watchlist_id IS NOT NULL
        )::integer AS "inventoryPublicCount"
      FROM public.watchlist AS w
      LEFT JOIN public.watchlist_visibility_0089_rollback AS rollback
        ON rollback.watchlist_id = w.id
    `;
    invariant(
      visibility?.publicCount === 0 && visibility.inventoryPublicCount === 0,
      "Rollback abort: database is no longer in the uniformly private state",
    );

    const restored = await tx<{ watchlistId: string }[]>`
      UPDATE public.watchlist AS w
      SET is_public = true
      FROM public.watchlist_visibility_0089_rollback AS rollback
      WHERE w.id = rollback.watchlist_id
        AND NOT w.is_public
      RETURNING w.id AS "watchlistId"
    `;
    invariant(
      restored.length === artifact.aggregates.publicCount,
      `Rollback restored ${restored.length} rows; expected ${artifact.aggregates.publicCount}`,
    );
    const [post] = await tx<{ expectedPublic: number; unexpectedPublic: number }[]>`
      SELECT
        count(*) FILTER (WHERE w.is_public AND rollback.watchlist_id IS NOT NULL)::integer
          AS "expectedPublic",
        count(*) FILTER (WHERE w.is_public AND rollback.watchlist_id IS NULL)::integer
          AS "unexpectedPublic"
      FROM public.watchlist AS w
      LEFT JOIN public.watchlist_visibility_0089_rollback AS rollback
        ON rollback.watchlist_id = w.id
    `;
    invariant(
      post?.expectedPublic === artifact.aggregates.publicCount &&
        post.unexpectedPublic === 0,
      `Rollback visibility postcondition failed: ${JSON.stringify(post)}`,
    );
    await tx`
      UPDATE public.watchlist_visibility_0089_state
      SET status = 'rolled_back'
      WHERE migration_key = ${migrationKey}
        AND status = 'private'
    `;
    return "rolled-back" as const;
  });

  process.stdout.write(
    `${JSON.stringify({
      command: "rollback",
      migration: targetTag,
      outcome,
      restoredPublicCount: artifact.aggregates.publicCount,
      artifactRetained: true,
    })}\n`,
  );
}

async function main(): Promise<void> {
  const args = process.argv.slice(2).filter((argument) => argument !== "--");
  const command = args[0];
  invariant(
    isCommand(command),
    "Usage: tsx scripts/watchlist-visibility-migration.ts <inventory|apply|verify|rollback> <inventory.json> [evidence.json]",
  );
  const inventoryPath = args[1];
  invariant(inventoryPath, `${command} requires an inventory path`);
  const databaseUrl = requireDirectDatabaseUrl(command);
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connect_timeout: 15,
    connection: { application_name: `jobseek-watchlist-visibility-${command}` },
  });

  try {
    if (command === "inventory") {
      await inventory(sql, inventoryPath);
      return;
    }
    const artifact = readArtifact(inventoryPath);
    if (command === "apply") {
      await applyMigration(sql, artifact);
    } else if (command === "verify") {
      await verify(sql, artifact, args[2]);
    } else {
      await rollback(sql, artifact);
    }
  } finally {
    await sql.end({ timeout: 5 });
  }
}

void main().catch((error: unknown) => {
  logExternalError(
    "error",
    { service: "database", operation: "watchlist_visibility_migration" },
    error,
  );
  process.exitCode = 1;
});
