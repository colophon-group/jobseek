export type MigrationIdentity = {
  createdAt: number;
  hash: string;
};

export type MigrationLedgerSnapshot = {
  latest: MigrationIdentity | null;
  matchingHeadRows: number;
  headTimestampRows: number;
};

export class MigrationHeadMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MigrationHeadMismatchError";
  }
}

function renderIdentity(identity: MigrationIdentity | null): string {
  if (!identity) return "empty ledger";
  return `${identity.createdAt}/${identity.hash.slice(0, 12)}`;
}

/**
 * Require production to match the exact migration head bundled with the
 * deployment artifact. Deploys must never apply migrations implicitly: some
 * migrations require protected evidence and human approval. Instead, this
 * check makes promotion fail closed until the reviewed migration path has
 * brought production to the same head.
 */
export function assertMigrationHead(
  expected: MigrationIdentity,
  observed: MigrationLedgerSnapshot,
): void {
  if (!observed.latest) {
    throw new MigrationHeadMismatchError(
      `Production migration ledger is empty; expected ${renderIdentity(expected)}. Apply reviewed migrations before promotion.`,
    );
  }

  if (observed.latest.createdAt !== expected.createdAt) {
    const direction = observed.latest.createdAt < expected.createdAt
      ? "behind"
      : "ahead of";
    throw new MigrationHeadMismatchError(
      `Production migration ledger is ${direction} the checked-out code: expected ${renderIdentity(expected)}, found ${renderIdentity(observed.latest)}. Refusing promotion.`,
    );
  }

  if (observed.latest.hash !== expected.hash) {
    throw new MigrationHeadMismatchError(
      `Production migration head hash differs from the checked-out migration at ${expected.createdAt}: expected ${expected.hash.slice(0, 12)}, found ${observed.latest.hash.slice(0, 12)}. Refusing promotion.`,
    );
  }

  if (
    observed.matchingHeadRows !== 1 ||
    observed.headTimestampRows !== 1
  ) {
    throw new MigrationHeadMismatchError(
      `Production migration head must be recorded exactly once; found ${observed.matchingHeadRows} exact rows and ${observed.headTimestampRows} rows at timestamp ${expected.createdAt}. Refusing promotion.`,
    );
  }
}
