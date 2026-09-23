import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  assertRoutineMigrationLedger,
  expectedRoutineMigrationConfirmation,
  loadRoutineMigrationPlan,
} from "../routine-migration";

const migrationFolder = resolve(process.cwd(), "drizzle");
const target = {
  tag: "0094_ai_filter_candidate_languages",
  createdAt: 1_790_082_600_000,
  hash: "e702743fc50bc5d70a37db5e0a406e2f890186446407b728360aa61cd344c66c",
};
const environment = {
  MIGRATION_REQUIRE_UNPOOLED: "true",
  ROUTINE_MIGRATION_REVISION: "1".repeat(40),
  ROUTINE_MIGRATION_TAG: target.tag,
  ROUTINE_MIGRATION_CREATED_AT: String(target.createdAt),
  ROUTINE_MIGRATION_HASH: target.hash,
  ROUTINE_MIGRATION_CONFIRMATION: expectedRoutineMigrationConfirmation(target),
};

describe("routine migration guard", () => {
  it("binds an allowlisted target to the exact checked-out journal head", () => {
    const plan = loadRoutineMigrationPlan(migrationFolder, environment);

    expect(plan?.target).toEqual(target);
    expect(plan?.localMigrationCount).toBe(83);
    expect(plan?.prerequisite.tag).toBe("0093_ai_filter_historical_horizon");
  });

  it.each([
    ["hash", { ROUTINE_MIGRATION_HASH: "0".repeat(64) }],
    ["timestamp", { ROUTINE_MIGRATION_CREATED_AT: "1789127975001" }],
    ["confirmation", { ROUTINE_MIGRATION_CONFIRMATION: "APPLY" }],
    ["revision", { ROUTINE_MIGRATION_REVISION: "deadbeef" }],
  ])("rejects a mismatched %s", (_field, override) => {
    expect(() =>
      loadRoutineMigrationPlan(migrationFolder, { ...environment, ...override }),
    ).toThrow();
  });

  it("fails closed when routine migration inputs are partial", () => {
    expect(() =>
      loadRoutineMigrationPlan(migrationFolder, {
        MIGRATION_REQUIRE_UNPOOLED: "true",
        ROUTINE_MIGRATION_REVISION: "1".repeat(40),
      }),
    ).toThrow(/ROUTINE_MIGRATION_TAG is required/);
  });

  it("requires the live ledger to be the exact expected transition", () => {
    const plan = loadRoutineMigrationPlan(migrationFolder, environment)!;
    const before = {
      latest: plan.prerequisite,
      prerequisiteExactRows: 1,
      prerequisiteTimestampRows: 1,
      prerequisiteHashRows: 1,
      targetExactRows: 0,
      targetTimestampRows: 0,
      targetHashRows: 0,
      rowsAfterPrerequisite: 0,
      rowsAfterTarget: 0,
    };
    const after = {
      ...before,
      latest: plan.target,
      targetExactRows: 1,
      targetTimestampRows: 1,
      targetHashRows: 1,
      rowsAfterPrerequisite: 1,
    };

    expect(() => assertRoutineMigrationLedger(plan, "preflight", before)).not.toThrow();
    expect(() => assertRoutineMigrationLedger(plan, "postflight", after)).not.toThrow();
    expect(() =>
      assertRoutineMigrationLedger(plan, "preflight", {
        ...before,
        rowsAfterPrerequisite: 1,
      }),
    ).toThrow(/unexpected later ledger rows/);
    expect(() =>
      assertRoutineMigrationLedger(plan, "postflight", {
        ...after,
        targetTimestampRows: 2,
      }),
    ).toThrow(/target identity has unexpected rows/);
  });
});
