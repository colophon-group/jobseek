import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { readMigrationFiles, type MigrationMeta } from "drizzle-orm/migrator";

export type RoutineMigrationIdentity = {
  tag: string;
  createdAt: number;
  hash: string;
};

export type RoutineMigrationLedgerRow = {
  createdAt: string | number;
  hash: string;
};

export type RoutineMigrationLedgerSnapshot = {
  latest: RoutineMigrationLedgerRow | null;
  prerequisiteExactRows: number;
  prerequisiteTimestampRows: number;
  prerequisiteHashRows: number;
  targetExactRows: number;
  targetTimestampRows: number;
  targetHashRows: number;
  rowsAfterPrerequisite: number;
  rowsAfterTarget: number;
};

export type RoutineMigrationPlan = {
  revision: string;
  prerequisite: RoutineMigrationIdentity;
  target: RoutineMigrationIdentity;
  localMigrationCount: number;
};

type Journal = {
  entries: Array<{ idx: number; tag: string; when: number }>;
};

type Registry = {
  migrations: RoutineMigrationIdentity[];
};

type RoutineMigrationEnvironment = Record<string, string | undefined>;

const SHA_40 = /^[0-9a-f]{40}$/;
const SHA_256 = /^[0-9a-f]{64}$/;

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function requiredEnvironment(
  environment: RoutineMigrationEnvironment,
  name: string,
): string {
  const value = environment[name];
  invariant(value, `${name} is required for a routine migration`);
  return value;
}

function parseCreatedAt(raw: string): number {
  invariant(/^\d+$/.test(raw), "ROUTINE_MIGRATION_CREATED_AT must be numeric");
  const value = Number(raw);
  invariant(
    Number.isSafeInteger(value) && value > 0,
    "ROUTINE_MIGRATION_CREATED_AT must be a positive safe integer",
  );
  return value;
}

function identitiesFromJournal(
  journal: Journal,
  migrations: MigrationMeta[],
): RoutineMigrationIdentity[] {
  invariant(
    journal.entries.length === migrations.length,
    "Drizzle journal and SQL migration counts differ",
  );

  return journal.entries.map((entry, index) => {
    const migration = migrations[index];
    invariant(entry.idx === index, `Drizzle journal index ${entry.idx} is out of order`);
    invariant(migration, `SQL migration ${entry.tag} is missing`);
    invariant(
      migration.folderMillis === entry.when,
      `Migration timestamp does not match journal entry ${entry.tag}`,
    );
    invariant(SHA_256.test(migration.hash), `Migration hash is invalid for ${entry.tag}`);
    return { tag: entry.tag, createdAt: entry.when, hash: migration.hash };
  });
}

export function expectedRoutineMigrationConfirmation(
  target: RoutineMigrationIdentity,
): string {
  return `APPLY-ROUTINE-WEB-MIGRATION:${target.tag}:${target.createdAt}:${target.hash}`;
}

export function loadRoutineMigrationPlan(
  migrationFolder: string,
  environment: RoutineMigrationEnvironment = process.env,
): RoutineMigrationPlan | null {
  const routineEnvironmentNames = [
    "ROUTINE_MIGRATION_REVISION",
    "ROUTINE_MIGRATION_TAG",
    "ROUTINE_MIGRATION_CREATED_AT",
    "ROUTINE_MIGRATION_HASH",
    "ROUTINE_MIGRATION_CONFIRMATION",
  ] as const;
  if (!routineEnvironmentNames.some((name) => environment[name])) return null;
  const targetTag = requiredEnvironment(environment, "ROUTINE_MIGRATION_TAG");

  invariant(
    environment.MIGRATION_REQUIRE_UNPOOLED === "true",
    "Routine migrations require MIGRATION_REQUIRE_UNPOOLED=true",
  );
  invariant(
    !environment.RETIREMENT_ATTESTATION_MODE,
    "Routine and retirement migration modes are mutually exclusive",
  );

  const revision = requiredEnvironment(environment, "ROUTINE_MIGRATION_REVISION");
  const createdAt = parseCreatedAt(
    requiredEnvironment(environment, "ROUTINE_MIGRATION_CREATED_AT"),
  );
  const hash = requiredEnvironment(environment, "ROUTINE_MIGRATION_HASH");
  const confirmation = requiredEnvironment(
    environment,
    "ROUTINE_MIGRATION_CONFIRMATION",
  );
  invariant(SHA_40.test(revision), "ROUTINE_MIGRATION_REVISION must be lowercase 40-hex");
  invariant(SHA_256.test(hash), "ROUTINE_MIGRATION_HASH must be lowercase SHA-256");

  const journal = JSON.parse(
    readFileSync(resolve(migrationFolder, "meta/_journal.json"), "utf8"),
  ) as Journal;
  const registry = JSON.parse(
    readFileSync(resolve(migrationFolder, "routine-migrations.json"), "utf8"),
  ) as Registry;
  const migrations = readMigrationFiles({ migrationsFolder: migrationFolder });
  const identities = identitiesFromJournal(journal, migrations);
  const target = { tag: targetTag, createdAt, hash };

  invariant(
    new Set(registry.migrations.map((entry) => entry.tag)).size ===
      registry.migrations.length,
    "Routine migration registry contains duplicate tags",
  );
  invariant(
    registry.migrations.some(
      (entry) =>
        entry.tag === target.tag &&
        entry.createdAt === target.createdAt &&
        entry.hash === target.hash,
    ),
    "Requested migration identity is not in the reviewed routine registry",
  );
  invariant(
    target.tag !== "0086_drop_supabase_job_posting",
    "The destructive 0086 retirement requires its dedicated workflow",
  );

  const localTarget = identities.at(-1);
  const prerequisite = identities.at(-2);
  invariant(localTarget, "No local Drizzle migrations were found");
  invariant(prerequisite, "A routine migration requires a local prerequisite");
  invariant(
    localTarget.tag === target.tag &&
      localTarget.createdAt === target.createdAt &&
      localTarget.hash === target.hash,
    "Requested routine migration must be the exact checked-out journal head",
  );
  invariant(
    confirmation === expectedRoutineMigrationConfirmation(target),
    "ROUTINE_MIGRATION_CONFIRMATION does not bind the exact target identity",
  );

  return {
    revision,
    prerequisite,
    target,
    localMigrationCount: identities.length,
  };
}

export function assertRoutineMigrationLedger(
  plan: RoutineMigrationPlan,
  phase: "preflight" | "postflight",
  snapshot: RoutineMigrationLedgerSnapshot,
): void {
  const expectedLatest = phase === "preflight" ? plan.prerequisite : plan.target;
  invariant(
    snapshot.latest &&
      Number(snapshot.latest.createdAt) === expectedLatest.createdAt &&
      snapshot.latest.hash === expectedLatest.hash,
    `Routine migration ${phase} expected latest ${expectedLatest.createdAt}/${expectedLatest.hash.slice(0, 12)}; found ${snapshot.latest?.createdAt ?? "none"}/${snapshot.latest?.hash.slice(0, 12) ?? "none"}`,
  );
  invariant(
    snapshot.prerequisiteExactRows === 1 &&
      snapshot.prerequisiteTimestampRows === 1 &&
      snapshot.prerequisiteHashRows === 1,
    `Routine migration ${phase} prerequisite identity is not unique: ${JSON.stringify(snapshot)}`,
  );
  const expectedTargetRows = phase === "preflight" ? 0 : 1;
  invariant(
    snapshot.targetExactRows === expectedTargetRows &&
      snapshot.targetTimestampRows === expectedTargetRows &&
      snapshot.targetHashRows === expectedTargetRows,
    `Routine migration ${phase} target identity has unexpected rows: ${JSON.stringify(snapshot)}`,
  );
  invariant(
    snapshot.rowsAfterPrerequisite === expectedTargetRows &&
      snapshot.rowsAfterTarget === 0,
    `Routine migration ${phase} found unexpected later ledger rows: ${JSON.stringify(snapshot)}`,
  );
}
