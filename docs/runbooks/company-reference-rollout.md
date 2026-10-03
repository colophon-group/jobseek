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

The executable protected rehearsal is `Operate Web PostgreSQL Backup (Hetzner)`
(`operate-web-postgresql-backup.yml`), mode `rehearse`, target
`0100_company_references`, confirmation `REHEARSE-COMPANY-REFERENCE-0100`.
The final contract uses target `0101_company_reference_selection_contract` and
confirmation `REHEARSE-COMPANY-REFERENCE-0101`; each confirmation authorizes only
its selected target. Its prerequisite is an expanded v3 packet at exact 0100.
First deploy the reviewed backup helper at current main through `Deploy Data
Backups`, service `web-postgresql`, then use protected `verify`, `backup` and
`restore` modes. A fresh encrypted **v3** packet must include retained
`company_description` and the complete six-FK inventory. Existing v1/v2 packets
remain restorable with their exact original boundaries, but cannot certify this
rehearsal.

The deploy workflow builds a self-contained bundle from locked dependencies and
actual verifier/normalizer sources, exact SQL, journal and consumer inventory.
Its manifest binds the clean source revision, every bundled resource and the
digest-pinned Node24 runtime; the host installs only root-owned read-only files.
The operation uses existing deployment/service locks and an internal isolated
PostgreSQL17 network. The helper receives only the disposable password file,
exact restore hostname and reviewed identities. It receives no production URL,
backup/SSH secret, host network or Docker socket and installs nothing at runtime.
Only isolated role scaffolding is added before actual preflight, bounded exact
SQL plus ledger transaction, and postflight. Every retained owner/filter/history
row count and digest must remain unchanged. The final-contract target must also
preserve every existing canonical reference, including source, verification and
creation/update timestamps. Proof must report the corresponding dependency phase;
its reference count must equal the seeded legacy count for 0100 or the preserved
canonical count for 0101.

A successful operation proves removal of both containers, the internal network,
credentials and restored plaintext before publishing its counts/digests artifact.
Apply Routine requires `rehearsal_run_id` from that successful owner-dispatched
main run. It verifies the workflow path, actor, current main SHA, attempt, fresh
completion (within nine hours), v3 archive identity, exact migration hash/timestamp,
rebuilt manifest/runtime identity, preservation and cleanup. It repeats this check
immediately before live DDL; changed main, failed/expired evidence, missing
inventory or a different target blocks apply. No rehearsal run is evidence of
successful production DDL. If main changes during isolated work, rerun against
the new reviewed revision; the stale operation cannot publish eligible evidence.

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
SHA-256 from `drizzle/routine-migrations.json`, plus the bound successful
`rehearsal_run_id`. Its confirmation binds all three migration fields.
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
Keep its immutable bridge-mode release as the expansion/bridge rollback floor.
This artifact is not the final reference-mode rollback floor. Exercise a dedicated
authenticated canary user/watchlist: a newly published company absent from both
web representations must survive first save, edit, star, sharing/reload and removal.
The deployment workflow first runs this full lifecycle against the immutable
staged URL, then verifies promotion owns `jseek.co`, and runs the same lifecycle
again through exactly `https://jseek.co` with a different company still absent
from both representations. Public mode uses normal authentication and never
requires or sends the automation bypass secret. It reattests the alias's exact
immutable deployment ID, source SHA and URL immediately before authenticated
requests and after lifecycle cleanup, using bounded read-only management API GETs.
Only aggregate phase/proof fields escape; raw provider/auth output and customer
identifiers stay private. A changed alias, unavailable identity lookup, missing
first-use fixture or failed cleanup fails the deployment. Identity checks bracket
the lifecycle; they do not certify that no outside operator changed the alias
briefly between checks. Do not change aliases concurrently with verification.
Before declaring bridge acceptance, require both canary contracts to pass and
retain the promoted immutable artifact for the applicable rollback phase.
Picker diagnostics use fixed scope/open/search/locate/click/close/persistence
phases and bounded error types, without locator text. Clone verification waits
for the sign-in import's committed copy and real redirect; it must not hard-reload
the overview while that import is in flight. Cleanup emits separate aggregate
recovery/deletion/residual/session evidence even when the lifecycle fails. More
than two exact-namespace rows remains an ambiguity failure requiring scoped
operator recovery; never broaden that cap to hide duplicate imports.
The proxy shares a 30/minute IP budget across watchlist navigation and browsing
actions, including authenticated requests. The remote deployment waits 65 seconds
before each staged/public lifecycle so cache smoke and the earlier canary do not
consume its starting burst budget. A canary document GET may retry once only on
an explicit HTTP429 with integer `Retry-After` of 1–65 seconds; an invalid/excessive
delay or second denial fails. Server Actions, form submissions and the title-query
navigation that initiates watchlist creation are never replayed. Removal/reload
must render an authenticated editable owner shell before an absent pill counts as
proof. Cleanup separately proves request identity, owner shell, deletion trigger,
confirmation and persisted absence. Sustained-hour limits still fail the gate.
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
identities still exist uniquely; the appropriate lifecycle catalogue contract must
still match. The existing daily protected `Web Database Migrations` drift job runs this audit
as `jobseek_migration_auditor`, using `DATABASE_URL_READONLY` and an explicit
audited runtime role (`postgres`). Expansion grants that existing auditor only
SELECT and a narrowly scoped RLS SELECT policy; it creates no role, elevates no
attributes and grants no write privileges. The verifier checks the exact policy,
role safety/default read-only setting and SELECT-only table ACL. Runtime role
ACLs are inspected independently from the auditor identity. No credentials belong
in an agent scheduler or public issue. The 0101 selection contract updates
phase-aware drift verification in the same PR. Drift accepts exactly the expanded bridge or contracted reference state,
requires each phase's exact migration identities, and refuses mixed ledger/schema
or weakened deletion semantics.

## Contract and rollback

The separate contract migration is `0101_company_reference_selection_contract`.
Before it can be dispatched, record the deployed compatible bridge SHA and a
successful authenticated mutation/reload canary bound to that deployment. Verify
that all active instances and retained rollback artifacts support durable
references; prevent rollback promotion to pre-bridge versions. Old catalogue
writer compatibility has now served its purpose. Capture membership/owner/filter
and saved-history count/digest evidence and retain a successful protected restore
drill. The contract migration must remain a separate PR/release from expansion.

Deploy the extended immutable rehearsal bundle from that reviewed current main,
then take and restore a fresh v3 backup in the expanded phase. Run protected
`rehearse` with target `0101_company_reference_selection_contract` and confirmation
`REHEARSE-COMPANY-REFERENCE-0101`. This rehearses the actual contract verifier and
SQL against the restored archive, preserving all owner/filter/history and existing
canonical reference fingerprints. The successful protected run must remain bound
to that exact current main, target SQL/timestamp, bundle/runtime and archive.

At the protected direct connection, run `verify-company-references.ts
contract-preflight`. It requires the exact 0100 bridge ledger head, verified
reference coverage, validated legacy company(id) cascade FKs, exact compatibility
trigger/function and read-only auditor policy/ACL. Dispatch the existing routine
workflow at current reviewed main with the exact 0101 tag/timestamp/hash,
confirmation and successful 0101 `rehearsal_run_id`; it automatically repeats
contract-preflight and protected proof verification before its final main
reauthorization, applies only that ledger-head migration, and runs
contract-postflight after verify-head. Do not dispatch until the recorded bridge
canary and rollback-floor evidence is current.

The DDL takes bounded exclusive locks, rechecks complete selected UUID coverage
and exact old FK semantics in its transaction, moves only watchlist/star company
FKs to immediate validated reference FKs with ON DELETE RESTRICT, and removes the
legacy seed trigger/function. It preserves UUIDs, memberships, timestamps,
owners, filters, history and inactive mirror relationships. It invents no
replacement identity. Lock contention/deadlock or any catalog mismatch aborts the
whole migration; use a quiet mutation window and retry the same reviewed identity
rather than weakening locks or bypassing checks.

After successful contract-postflight, run the authenticated canary again. Switch
`COMPANY_REFERENCE_WRITE_MODE` to `reference` only through a reviewed protected
web deployment; this is the point at which new sparse references cease receiving
legacy rows. Vercel bakes the write mode into each deployment's environment;
changing the current project setting does not change an older artifact. Record
a successful immutable build of the compatible code with reference mode against
0101 as the final rollback floor, including its commit, deployment ID and canary
evidence. The original bridge-mode artifact cannot serve as that floor: a new
reference can reuse a slug already owned by another legacy UUID, and its later
edit/copy/star would then fail the legacy unique constraint. Verify a company
with a reused slug can be selected without creating a
legacy row, legacy catalogue deletion cannot erase selections, selected reference
deletion is restricted, and retiring search never broadens scope. Scheduled drift
now checks the contract phase, zero missing selected references and absence of the
retired compatibility trigger/function. It no longer demands legacy catalogue
parity or legacy INSERT privileges from the reference-only runtime. Independently
inventory/retire inactive mirror consumers before considering dropping `company`.

During expansion/bridge, application rollback retains both tables and trigger.
Do not undo the seed, remove references, delete legacy rows or reset provenance.
If migration fails, its transaction leaves schema/data/ledger unchanged; correct
preflight and retry the same reviewed identity. A partial application transaction
also rolls back reference/legacy/membership together.

The real PostgreSQL rollback contract exercises the unchanged compatible service
code against exact 0101 in reference mode, retaining a reference-only reused-slug
selection created after cutover through offline edit, copy and star/unstar. It
also proves bridge-mode rejection leaves selections unchanged, and preserves
pre-cutover ownership, filters, memberships and saved/interview history. This
local service proof must be paired with the exact immutable floor's deployed
authenticated canary; source compatibility alone does not certify an artifact.

After the final contract, rollback stays at that recorded reference-mode release
or uses a reviewed forward repair/rebuild at current approved main. Until the
immutable reference-mode floor exists, recovery is that reviewed forward rebuild;
never promote the original bridge-mode artifact. Stop final rollout closure if
the floor's identity, mode or canary evidence is missing. Never restore historical
cascade FKs or pre-bridge app
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


## Owner-shell diagnostic follow-up (#10247)

The failed BIH diagnostic committed the reference and selection, completed the
initial persisted reload and later edit/share/clone/star operations, then timed
out after removal while waiting for owner UI. SQL membership absence and HTTP
2xx do not prove that the account header and exact edited title rendered. The
failure remains a failed lifecycle; cleanup success does not convert it to a pass.

The shared canary now reports separate `owner_header` and `owner_title` phases.
Before cleanup it emits a bounded readiness record: route/origin equality (no
URL), navigation status/time, visible and DOM-visible role counts, loading and
modal counts, persisted-title equality, membership count/any-company mode, and
normal session status/identity equality. Supplemental reads have a two-second
budget and never replace the original wait failure. Existing locator budgets,
identity/ownership checks, single-shot mutations, deletion and signout remain
required. The final cleanup proof independently rejects residual fixtures,
missing star restoration or an unclosed session even if owner readiness passes.

Passive network evidence records only same-origin session GET and opaque
Server Action **candidate** status/latency/pending/failure, capped at eight
requests and eight browser errors per navigation with an explicit truncation
flag. It cannot identify a particular Server Action as bootstrap. Browser errors
are fixed categories; only an already available numeric Next digest may appear.
No messages, DOM text, titles, identifiers, response bodies, headers, cookies,
URLs, credentials or stacks enter diagnostic output. A generic external logger's
`timeout=false` classifies transport codes and cannot rebut a Playwright timeout.

An owner-governed follow-up may first create an isolated dedicated-account
fixture through normal UI, verify paused notifications, exact owner/namespace and
pinned deployment identity, then call the exported `diagnoseCanaryOwnerReload`
helper with that fixture's private expected path/title/origin and bounded
read-state/evidence callbacks. This helper performs one explicit document GET
(with only the existing bounded 429 GET retry), waits for the unchanged owner
conditions and reports `firstUse=false`, `existing_reference`, `readOnlyReload`.
Re-attest public alias identity after the diagnostic. The helper sends no
mutation; normal page bootstrap may still make its own read Server Action.

BIH's canonical reference now exists. Any subsequent BIH lifecycle must label
its `CanaryLifecycleState.referenceCoverage` as `existing_reference`; never delete
it or another canonical identity to force novelty. Read-only reload or existing
reference coverage cannot qualify as production promotion first-use. The
protected runner retains its mandatory novel catalogue selection, pre-save
absence check, committed Typesense-provenance proof and `firstUse=true` success
contract. After diagnosis, at most one separately authorized full lifecycle may
run on a fresh dedicated fixture, with exact cleanup. Change application behavior
only when instrumented evidence supports a concrete defect; do not expand waits
or replay saves/removal just to obtain green.
