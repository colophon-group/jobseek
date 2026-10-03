# Company reference expansion, bridge and promotion

Epic #10222; [ownership ADR](../adr/008-company-reference-ownership.md);
[consumer inventory](../company-reference-dependencies.md). The expansion is
additive and intentionally precedes the bridge deployment. Do not mark #10214
resolved after expansion alone.

## Prepare reviewed evidence

Run the exact checked-out migration test against a disposable localhost database
whose name ends in `_fixture`:

```sh
COMPANY_REFERENCE_MIGRATION_TEST_DATABASE_URL='<disposable-direct-postgres-url>' \
  pnpm exec vitest run src/db/__tests__/company-reference-migration.test.ts
```

The test executes the actual SQL and exercises seed metadata/timestamps, unchanged
memberships/history, failed seed rollback, late old-version writes, provenance
protection, bounds and duplicate slugs, selection transaction rollback and drift.
Producer/consumer integration and authenticated browser tests must also pass.
Restore a recent protected backup into an isolated database and rerun preflight,
expansion and postflight there before production. Keep private backup credentials
and row values out of CI/public artifacts; publish counts/digests only.

With the protected direct web database environment, run:

```sh
pnpm exec tsx scripts/verify-company-references.ts preflight /secure/preflight.json
```

Preflight requires the exact 0099 ledger head, no unrecorded reference table or
function, well-formed legacy name/slug/icon, and the two unchanged company FKs. It also privately streams bounded batches through the actual public read normalizer and refuses any nonrenderable reference; only aggregate counts are emitted.
Unexpected ledger state, malformed metadata or an unreviewed schema conflict
blocks apply. Fix the underlying reviewed source and rerun; do not edit the ledger
or replace IDs. Expansion locks legacy company writes while installing the seed
and trigger; the runner sets bounded lock/statement timeouts.

## Apply expansion through the protected workflow

Merge the reviewed expansion PR only after required CI and current head/base
checks. Use `Apply Routine Web Migration` (`apply-web-routine-migration.yml`) at
current main with its exact journal tag `0100_company_references`, timestamp and
SHA-256 from `drizzle/routine-migrations.json`. Its confirmation binds all three.
The workflow owner dispatch and `production-migrations`/`production` environments
are the authorization/execution boundary. Do not use `db:push`, an ad hoc SQL
session, crawler sync, mutable tags or live copies as a replacement.

Immediately run and retain:

```sh
pnpm exec tsx scripts/verify-company-references.ts postflight /secure/postflight.json
```

Postflight checks the exact ledger identity/head, permanent RLS table, UUID/default
and every column/check/PK/index, exact compatibility function/trigger, unchanged
legacy FKs, zero missing legacy/selection references, exact seeded display fields,
and connected runtime/browser access. The migration converts legacy timezone-naive
timestamps as UTC, preserving intended instants. Run with the actual web runtime
role for the privilege assertion; an owner-only check cannot certify a different
application role. Production schema owner retains privileges; restricted runtime
roles need separately reviewed explicit grants and matching RLS policy before
promotion. Never grant browser access to fix a runtime-role failure.

## Promote the compatible bridge

Only deploy the bridge after successful postflight. It writes reference and
minimal legacy rows atomically with selections; reads durable reference display.
Bridge writes take each sorted UUID in legacy-company then reference order, matching
the compatibility trigger. Its transient legacy seed is promoted to verified
Typesense provenance in the same transaction; reference-only mode skips legacy
writes after contract. This ordering prevents coexistence deadlocks.
Keep its immutable release available as the rollback floor. Exercise a dedicated
authenticated canary user/watchlist: a newly published company absent from both
web representations must survive first save, edit, star, sharing/reload and removal.
Verify partial/unknown lookup failures leave counts/filters unchanged, and existing
reference edit/removal works without search. Never mutate the reporter's watchlist
as a canary. Compare persisted membership UUID/count digests before/after promotion.

Observe classified first-use/lookup/foreign-key failures without personal metadata.
Selection entry points emit `company_selection_mutation` with only bounded
`operation` and `outcome` fields, after the service resolves or rejects. Count
these events by operation/outcome through the established production log surface;
do not export raw request, user, watchlist or company payloads. Handoff delegates
to the unobserved create implementation so one handoff does not also count as a
separate create. Outcomes distinguish lookup miss/unavailability, identity
conflict, authorization/nonexistence, capacity, malformed input, foreign-key
failure and other database failure. A logging failure cannot alter the mutation
result. Capture this aggregate evidence and reference coverage at 24 hours and
7 days before closing rollout observation.
A successful page read or unit test does not establish this write contract. Re-run
read-only aggregate drift checks after application deployment and catalogue
publication changes:

```sh
COMPANY_REFERENCE_RUNTIME_ROLE=postgres \
  pnpm exec tsx scripts/verify-company-references.ts drift /secure/drift.json
```

`drift` permits later ledger entries only when the exact expansion/prerequisite
identities still exist uniquely; the expansion-phase catalogue contract must still
match. The existing daily protected `Web Database Migrations` drift job runs this audit
as `jobseek_migration_auditor`, using `DATABASE_URL_READONLY` and an explicit
audited runtime role (`postgres`). Expansion grants that existing auditor only
SELECT and a narrowly scoped RLS SELECT policy; it creates no role, elevates no
attributes and grants no write privileges. The verifier checks the exact policy,
role safety/default read-only setting and SELECT-only table ACL. Runtime role
ACLs are inspected independently from the auditor identity. No credentials belong
in an agent scheduler or public issue. A future FK contract
migration must update phase-aware drift verification in the same PR.

## Contract and rollback

Separate the restrictive watchlist/star FK migration from expansion. Require all
active writer/reader cutovers, authenticated canaries, membership preservation,
and rollback compatibility first. Catalogue disappearance must then be proven
unable to delete selections. Stop legacy writes only after the rollback floor
supports on-demand sparse references; independently inventory/retire inactive
mirror consumers before considering dropping `company`.

During expansion/bridge, application rollback retains both tables and trigger.
Do not undo the seed, remove references, delete legacy rows or reset provenance.
If migration fails, its transaction leaves schema/data/ledger unchanged; correct
preflight and retry the same reviewed identity. A partial application transaction
also rolls back reference/legacy/membership together.

After the final contract, rollback stays at the compatible bridge release or uses
a reviewed forward repair. Never restore historical cascade FKs or pre-bridge app
releases as an automatic rollback. Schema restoration is a protected backup/restore
operation with preserved selection data, not the destructive inverse of expansion.

## Dependency retirement gate

Before retiring any company producer, update the executable lifecycle manifest
linked from the dependency inventory. Every retained FK must declare an owner
and compatible replacement or independent retained-history disposition. The
read-only audit compares the complete inbound catalog against this inventory;
uninventoried, missing or weakened relationships block promotion. Saved-job
company snapshots must remain complete and independent of all company-ID FKs.
The final-contract audit uses reference mode only after its exact ledger proves
0101; reference mode refuses the active legacy selection FKs. Require the actual
Drizzle-schema PG fixture and behavior/canary evidence before retiring writers.
