import { describe, expect, it } from "vitest";

import {
  assertMigrationHead,
  MigrationHeadMismatchError,
  type MigrationLedgerSnapshot,
} from "../migration-head";

const expected = {
  createdAt: 1_788_199_156_000,
  hash: "a".repeat(64),
};

function snapshot(
  overrides: Partial<MigrationLedgerSnapshot> = {},
): MigrationLedgerSnapshot {
  return {
    latest: expected,
    matchingHeadRows: 1,
    headTimestampRows: 1,
    ...overrides,
  };
}

describe("production migration head guard", () => {
  it("accepts one exact production head", () => {
    expect(() => assertMigrationHead(expected, snapshot())).not.toThrow();
  });

  it("rejects an empty or behind production ledger", () => {
    expect(() =>
      assertMigrationHead(expected, snapshot({ latest: null })),
    ).toThrow(MigrationHeadMismatchError);
    expect(() =>
      assertMigrationHead(expected, snapshot({
        latest: { createdAt: expected.createdAt - 1, hash: "b".repeat(64) },
      })),
    ).toThrow(/behind the checked-out code/);
  });

  it("rejects a database ahead of the checked-out deployment", () => {
    expect(() =>
      assertMigrationHead(expected, snapshot({
        latest: { createdAt: expected.createdAt + 1, hash: "b".repeat(64) },
      })),
    ).toThrow(/ahead of the checked-out code/);
  });

  it("rejects a different SQL hash at the expected timestamp", () => {
    expect(() =>
      assertMigrationHead(expected, snapshot({
        latest: { createdAt: expected.createdAt, hash: "b".repeat(64) },
        matchingHeadRows: 0,
      })),
    ).toThrow(/head hash differs/);
  });

  it("rejects missing, duplicate, or colliding head ledger rows", () => {
    for (const observed of [
      snapshot({ matchingHeadRows: 0, headTimestampRows: 0 }),
      snapshot({ matchingHeadRows: 2, headTimestampRows: 2 }),
      snapshot({ matchingHeadRows: 1, headTimestampRows: 2 }),
    ]) {
      expect(() => assertMigrationHead(expected, observed)).toThrow(
        /recorded exactly once/,
      );
    }
  });
});
