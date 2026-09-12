export type AccountIssuerMigrationIdentity = {
  tag: string;
  createdAt: number;
  hash: string;
};

export type AccountIssuerLedgerEvidence = {
  rowCount: number;
  latestCreatedAt: string | null;
  latestHash: string | null;
};

export type AccountIssuerMigrationRowsEvidence = {
  prerequisiteExact: number;
  prerequisiteTimestamp: number;
  prerequisiteHash: number;
  targetExact: number;
  targetTimestamp: number;
  targetHash: number;
};

export type AccountIssuerPostTargetRow = {
  createdAt: string | number;
  hash: string;
};

type AccountIssuerLedgerTransition = {
  prerequisite: AccountIssuerMigrationIdentity;
  target: AccountIssuerMigrationIdentity;
  expectedPreflightRowCount: number;
  localPostTargetMigrations: AccountIssuerMigrationIdentity[];
};

function identityMatches(
  observed: AccountIssuerPostTargetRow,
  expected: AccountIssuerMigrationIdentity,
): boolean {
  return (
    Number(observed.createdAt) === expected.createdAt &&
    observed.hash === expected.hash
  );
}

function identitiesAreUnique(
  rows: AccountIssuerMigrationRowsEvidence,
  expectedTargetRows: 0 | 1,
): boolean {
  return (
    rows.prerequisiteExact === 1 &&
    rows.prerequisiteTimestamp === 1 &&
    rows.prerequisiteHash === 1 &&
    rows.targetExact === expectedTargetRows &&
    rows.targetTimestamp === expectedTargetRows &&
    rows.targetHash === expectedTargetRows
  );
}

export function isExactAccountIssuerPreLedger(
  ledger: AccountIssuerLedgerEvidence | undefined,
  rows: AccountIssuerMigrationRowsEvidence | undefined,
  transition: AccountIssuerLedgerTransition,
): boolean {
  return Boolean(
    ledger &&
      rows &&
      ledger.rowCount === transition.expectedPreflightRowCount &&
      Number(ledger.latestCreatedAt) === transition.prerequisite.createdAt &&
      ledger.latestHash === transition.prerequisite.hash &&
      identitiesAreUnique(rows, 0),
  );
}

export function isExactAccountIssuerPostLedger(
  ledger: AccountIssuerLedgerEvidence | undefined,
  rows: AccountIssuerMigrationRowsEvidence | undefined,
  postTargetRows: AccountIssuerPostTargetRow[],
  transition: AccountIssuerLedgerTransition,
): boolean {
  if (!ledger || !rows || !identitiesAreUnique(rows, 1)) return false;

  const { localPostTargetMigrations, target } = transition;
  if (
    localPostTargetMigrations.length === 0 ||
    localPostTargetMigrations[0]?.tag !== target.tag ||
    !identityMatches(localPostTargetMigrations[0], target)
  ) {
    return false;
  }

  const latestIndex = localPostTargetMigrations.findIndex(
    (migration) =>
      Number(ledger.latestCreatedAt) === migration.createdAt &&
      ledger.latestHash === migration.hash,
  );
  if (latestIndex < 0) return false;

  const expectedRows = localPostTargetMigrations.slice(0, latestIndex + 1);
  return (
    postTargetRows.length === expectedRows.length &&
    postTargetRows.every((row, index) =>
      identityMatches(row, expectedRows[index]!),
    )
  );
}
