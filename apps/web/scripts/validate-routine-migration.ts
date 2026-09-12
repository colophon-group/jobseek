import { resolve } from "node:path";

import dotenv from "dotenv";

import {
  expectedRoutineMigrationConfirmation,
  loadRoutineMigrationPlan,
} from "../src/db/routine-migration";
import { logExternalError } from "../src/lib/safe-external-error";

dotenv.config({ path: ".env.local", quiet: true });

function main(): void {
  const plan = loadRoutineMigrationPlan(resolve(process.cwd(), "drizzle"));
  if (!plan) throw new Error("ROUTINE_MIGRATION_TAG must be set");

  process.stdout.write(`${JSON.stringify({
    event: "routine_migration_target_validated",
    revision: plan.revision,
    migration: plan.target,
    prerequisite: plan.prerequisite,
    localMigrationCount: plan.localMigrationCount,
    confirmation: expectedRoutineMigrationConfirmation(plan.target),
  })}\n`);
}

try {
  main();
} catch (error: unknown) {
  logExternalError(
    "error",
    { service: "database", operation: "validate_routine_migration" },
    error,
  );
  process.exitCode = 1;
}
