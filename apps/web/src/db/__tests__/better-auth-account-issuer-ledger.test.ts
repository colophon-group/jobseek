import { describe, expect, it } from "vitest";

import {
  isExactAccountIssuerPostLedger,
  isExactAccountIssuerPreLedger,
  type AccountIssuerMigrationRowsEvidence,
} from "../better-auth-account-issuer-ledger";

const prerequisite = {
  tag: "0086_drop_supabase_job_posting",
  createdAt: 1_785_760_800_000,
  hash: "6".repeat(64),
};
const target = {
  tag: "0087_better_auth_account_issuer",
  createdAt: 1_787_560_116_000,
  hash: "7".repeat(64),
};
const later = [
  {
    tag: "0088_notification_policy_foundation",
    createdAt: 1_788_199_156_000,
    hash: "8".repeat(64),
  },
  {
    tag: "0089_watchlist_unlisted_sharing",
    createdAt: 1_789_050_000_000,
    hash: "9".repeat(64),
  },
];
const transition = {
  prerequisite,
  target,
  expectedPreflightRowCount: 76,
  localPostTargetMigrations: [target, ...later],
};
const uniquePostRows: AccountIssuerMigrationRowsEvidence = {
  prerequisiteExact: 1,
  prerequisiteTimestamp: 1,
  prerequisiteHash: 1,
  targetExact: 1,
  targetTimestamp: 1,
  targetHash: 1,
};
const observed = [target, ...later].map(({ createdAt, hash }) => ({
  createdAt,
  hash,
}));

describe("Better Auth account issuer ledger state", () => {
  it("preserves the strict post-0086 preflight state", () => {
    expect(
      isExactAccountIssuerPreLedger(
        {
          rowCount: 76,
          latestCreatedAt: String(prerequisite.createdAt),
          latestHash: prerequisite.hash,
        },
        {
          ...uniquePostRows,
          targetExact: 0,
          targetTimestamp: 0,
          targetHash: 0,
        },
        transition,
      ),
    ).toBe(true);

    expect(
      isExactAccountIssuerPreLedger(
        {
          rowCount: 77,
          latestCreatedAt: String(prerequisite.createdAt),
          latestHash: prerequisite.hash,
        },
        { ...uniquePostRows, targetExact: 0, targetTimestamp: 0, targetHash: 0 },
        transition,
      ),
    ).toBe(false);
  });

  it.each([
    ["the 0087 target", 0],
    ["a valid 0088 head", 1],
    ["a valid 0089 head", 2],
  ])("accepts %s", (_description, latestIndex) => {
    const latest = transition.localPostTargetMigrations[latestIndex]!;
    expect(
      isExactAccountIssuerPostLedger(
        {
          rowCount: 77 + latestIndex,
          latestCreatedAt: String(latest.createdAt),
          latestHash: latest.hash,
        },
        uniquePostRows,
        observed.slice(0, latestIndex + 1),
        transition,
      ),
    ).toBe(true);
  });

  it.each([
    ["missing target", { targetExact: 0 }],
    ["duplicate target", { targetExact: 2 }],
    ["target timestamp conflict", { targetTimestamp: 2 }],
    ["target hash conflict", { targetHash: 2 }],
    ["prerequisite timestamp conflict", { prerequisiteTimestamp: 2 }],
  ])("rejects %s", (_description, override) => {
    expect(
      isExactAccountIssuerPostLedger(
        {
          rowCount: 79,
          latestCreatedAt: String(later[1]!.createdAt),
          latestHash: later[1]!.hash,
        },
        { ...uniquePostRows, ...override },
        observed,
        transition,
      ),
    ).toBe(false);
  });

  it("rejects a ledger behind 0087, an unknown head, and a missing intermediate", () => {
    expect(
      isExactAccountIssuerPostLedger(
        {
          rowCount: 76,
          latestCreatedAt: String(prerequisite.createdAt),
          latestHash: prerequisite.hash,
        },
        uniquePostRows,
        [],
        transition,
      ),
    ).toBe(false);
    expect(
      isExactAccountIssuerPostLedger(
        {
          rowCount: 80,
          latestCreatedAt: "1789999999999",
          latestHash: "a".repeat(64),
        },
        uniquePostRows,
        observed,
        transition,
      ),
    ).toBe(false);
    expect(
      isExactAccountIssuerPostLedger(
        {
          rowCount: 79,
          latestCreatedAt: String(later[1]!.createdAt),
          latestHash: later[1]!.hash,
        },
        uniquePostRows,
        [observed[0]!, observed[2]!],
        transition,
      ),
    ).toBe(false);
  });

  it.each([78, 80])("rejects a valid suffix with malformed row count %i", (rowCount) => {
    expect(
      isExactAccountIssuerPostLedger(
        {
          rowCount,
          latestCreatedAt: String(later[1]!.createdAt),
          latestHash: later[1]!.hash,
        },
        uniquePostRows,
        observed,
        transition,
      ),
    ).toBe(false);
  });
});
