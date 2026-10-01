# Native ordinary worker authority foundation

This unselected library extends the existing Redis queue and PostgreSQL write
contracts. It adds no executable, production profile selection or separate ready
queue. Python ordinary workers remain the production owners until supported
quiesced cutover installs exclusive native selection and complete processing.

## Attempt identity and existing queue contracts

`Claim` retains the tokenless legacy ABI. `ClaimFenced` allocates a private random
128-bit attempt token, uses Redis TIME, and returns the stored lease deadline.
The same token must match an unexpired lease in heartbeat and settlement. The
`inflight_tokens:<wtype>` index extends existing lease state; ready queues,
domain fairness and source identity retain their existing representations.
Legacy heartbeat/completion/reschedule cannot modify a tokenized attempt.

The client disables mutation retries, bounds I/O, and retains the claimed task
when configuration loading fails. Callers must handle a partial task/error pair
without accidentally abandoning an inflight claim. Descriptor configuration is
a detached snapshot; authority descriptors do not expose the claim token.
Errors omit connection strings and upstream configuration content.

## Native database authority

Migration `0035` retains a fence for a monitor inventory or a detail attempt in
`ordinary_worker_write_fence`. It records the existing global routing epoch,
board identity, attempt token, configuration digest, completion state and
canonical next deadline. The SQL embedded in Go must match the applied migration
byte for byte. Its trigger rejects persisted fences from retired epochs.

`OpenAuthority` verifies the schema and expected current epoch before claiming
work, with one owned PostgreSQL connection. The expected epoch must come from a
verified active ownership plan; reading and adopting the allocator's latest
value is insufficient. The library reuses the B0 routing sequence and retirement
barrier instead of introducing another allocator. Any supported B0 epoch
retirement also retires ordinary authority. Selection/startup/cutover must
coordinate these owners before native ordinary work is admitted.

Transactions take locks in this order: ordinary lease barrier, existing routing
epoch barrier, canonical board/detail rows, retained fence. Claim, heartbeat and
writes hold shared barriers; settlement and the production Go lease reaper take
the lease barrier exclusively. Epoch retirement takes the existing routing
barrier exclusively. This orders lease removal and epoch retirement before or
after the entire native transaction. All participating writers must acquire the
barriers before row locks; the fence trigger is an additional epoch check.

Native writes run inside a bounded transaction with fresh Redis configuration
and token checks before and after the callback. Canonical detail-to-board
mapping is locked and verified. Fetch/render/enrichment must run outside the
transaction. The callback owns the actual native canonical effects; this library
does not implement monitor inventory, enrichment, disappearance or failure
policy. Terminal writes retain the database-owned `next_check_at` or
`next_scrape_at` and return an opaque receipt. Settlement accepts only a matching
committed receipt whose deadline still equals the canonical database deadline.
A NULL detail deadline completes rather than reschedules the task.

After a committed transaction loses its acknowledgement, a subsequent attempt
can recover an unchanged future canonical deadline or NULL completion, including
across epoch retirement, without repeating effects. Changed configuration,
board mapping or deadline prevents receipt recovery. Nonterminal streamed writes
require native processing to preserve its own idempotence and completeness
contracts. Raw queue methods are not a PostgreSQL write grant.

The production Go reaper holds the database barrier through its Redis sweep.
Legacy direct Python reaping skips tokenized attempts while continuing legacy
work. Lua's optional `guarded` argument identifies the wrapper's sweep; it is not
a secret or security capability. Every native lease-ending path must participate
in the database barrier. Old deployed scripts unaware of tokens cannot safely
coexist with native selection.

## Verification and its limits

Migration `0036` retains immutable staged/active/retired ownership documents in
`ordinary_worker_ownership_plan`. The document binds the global epoch, exact
source revision, supported member identities and stable configuration hashes.
PostgreSQL checks the exact payload SHA256 and required envelope fields; Go
rejects duplicate keys, unknown fields, unsupported members and noncanonical
serialization. Only one plan may be active. Retired identities cannot be
reactivated or deleted. State transitions take the existing lease and epoch
barriers; activation requires the current allocated epoch. Ordinary readback
uses those barriers without a conflicting ownership row lock.

`StageGreenhouseOwnership` captures enabled active canonical boards and their
matching Redis settings as one staged document. Partial inventories roll back;
staging allocates no epoch, changes no queue and grants no authority.
`LoadActiveOwnership` requires an exact caller-attested plan/revision and the
already verified epoch. It never adopts the allocator's latest value.
Generic unbound Go claims, writes, heartbeat and settlement reject an active
plan before queue mutations or callbacks.

`OpenOwnedAuthority` binds a native claimant to the exact active document and
Redis byte projection. Claims inspect 64 cohort members per batch, verify the
canonical enabled profile under a row lock, and atomically compare the complete
current board hash before removal. Missing first-time scores remain distinct
from a due recurring score or a score of zero. Writes/heartbeat/settlement
reattest ownership, canonical eligibility and projection before their effects.

Legacy pipeline startup rejects active ownership unless all four installed
identity fields match: `ORDINARY_OWNERSHIP_PLAN_SHA256`,
`ORDINARY_OWNERSHIP_PROJECTION_SHA1`, `ORDINARY_OWNERSHIP_SOURCE_REVISION` and
`ORDINARY_OWNERSHIP_ROUTING_EPOCH`. Every legacy claim holds the shared
DB lease/epoch barriers through its Lua pop. Unselected claims refuse any
unfinished joint transition. A journalled legacy owner also requires protected
`ORDINARY_GO_B0_AUDIT_LUA_FILE` bytes and freshly re-attests the exact active
journal, retained B0 target, canonical/Redis configurations and the same actual
B0 audit/permanent shared witnesses. Legacy write/settlement remains governed
by complete all-writer drain/stop before ownership changes. SHA1 is Redis's available byte
integrity check bound to the SHA256-attested durable payload, not a credential.
Neither owner reconstructs missing routing state or adopts a new allocator.

The existing Lua ABI now filters installed owners before removing tasks. Legacy
membership exclusion survives effective config/domain/browser-route drift.
Native candidates bypass foreign domain/task heads without touching their source
scores/configurations; legacy cursors advance bounded domain and 64-task windows.
Global first-time priority, the eight-monitor fairness counter, shared throttle,
scrape rotation and protected B0/inflight duplicate repair remain shared. Foreign
work at a higher global priority waits for its owner rather than being popped.
Unaware callers reject an installed projection; both planned callers reject
missing/corrupt routing, including complete Redis loss. Supported activation
must stop and replace every old writer before installing these expectations.

There is no supported production selection wrapper for this library. Before activation,
the supported all-writer release must install the exact startup/projection
identities and full native processing, prove installed ownership and coordinate
B0's shared epoch. Private tests use direct SQL for isolated owner fixtures and
the journalled native API for joint publication/activation fixtures.

Private Redis race tests cover both worker queues, attempt expiry/reclaim,
stale heartbeat/completion/reschedule, exact scheduling, deferred monitor
repair, duplicate representations, deadletters/orphans/guard cleanup, corrupt
index preflight and cancellation. Fixtures own private processes/directories and
never clear a shared Redis database.

Real migrated PostgreSQL/Redis tests cover monitor/detail transactions in both
queues, canonical posting/description/upload-state/board effects, exact
settlement, callback/cancellation rollback, configuration changes, delayed
activation after expiry, retirement ordered after commit, stale epochs rejected
by the schema, guarded reaper exclusion and commit-before-ack recovery across
retirement. The production Go `Sweep` wrapper has its own barrier regression.
Migration upgrade/downgrade/re-upgrade is checked against the actual schema.
Ownership tests additionally verify immutable transitions, single active
membership, stale epoch/source rejection, malformed SQL envelopes, rollback of
partial staging, unbound operation exclusion and transition/readback ordering.
Selection tests additionally cover mixed owners beyond domain/task windows,
native/legacy claim races, exact owned write/settlement, delayed disable before
pop, source/plan/epoch/projection revocation before effects and real Python
legacy attestation/loss handling. Both Linux jobs require those Python fixtures.
These are authority fixtures, not extraction/enrichment parity or an actual
native ordinary process SIGKILL/cold-reversal proof.

The `Go ordinary queue contracts` workflow requires real private Redis and an
actually migrated PostgreSQL 17 fixture on Linux amd64 and arm64. Missing required
fixtures fail. Local authority race tests use PostgreSQL 18.6 and Redis 8.10.1.
A skip or an earlier revision's green run establishes no new-head proof. Record
exact-head CI results in the PR before considering selection.

## Continuation gates

Migration `0039` and the native publication primitives now extend the retained
journal through `reserved → publishing → published → active`. A source-pinned
`ColdB0Target` captures each fixed producer selector's canonical enabled browser
configuration and matching Redis settings. The target binds exact board UUIDs,
configuration hashes, namespace, shard and cohort; epoch comes only from the
durable reservation. Unknown/duplicate JSON fields, nested metadata duplicates,
unsupported selectors and changed configurations fail admission. Metadata
numbers normalize by exact mathematical value across JSONB/Redis spellings,
including precise integers beyond float64. Normalization bounds exponent
magnitude at one million and uses scientific notation outside decimal exponents
-6 through 20, preventing enormous zero expansion. A shared Go/Python corpus
verifies exact bytes/hashes and malformed metadata rejection. Newly changed
target hashes must be captured/approved before intent, never adopted at runtime.

`PrepareColdOwnershipPublication` first installs an exact pending Redis witness
against the prior ordinary projection, then commits `publishing`. Only this
committed phase permits projection effects. `PublishColdOwnership` wraps the
unmodified, SHA256-pinned production B0 Lua conservation audit and projection
CAS in one EVAL. It requires the exact new shared-epoch route/producer selectors,
1–1600 conserved records and no inflight/dead work. One `MSET` publishes ordinary
projection and joint witness together. Acknowledged synchronous `SAVE` and fresh
atomic readback precede `published`. A failed SAVE/database commit leaves an
inspectable attempt; missing, expired, corrupt or partial witnesses are contained
instead of reconstructed. `ActivateColdOwnership` commits the exact ordinary DB
owner and active journal together after fresh publication/profile/B0 readback.
It does not start services or claim host/release readiness.

An exact successor can supersede only the prior active journal's plan/epoch and
target release. Its pending publication must replace the exact prior witness;
an old B0 epoch is rejected until the supported host has transferred that owner.
Private tests exercise SAVE denial/retry, missing Redis witnesses, post-SAVE SQL
failure/retry, atomic activation rollback/retry, wrong B0 route/selectors/records,
canonical/Redis config drift and corrupt routing types. A real private Redis
no-save shutdown and fresh process verify durable RDB projection/witness and B0
records/guards. Canonical rows/deadlines/receipts and every other Redis key remain
unchanged. Protected source-bound native coordinator commands now expose these
primitives, with real executable SIGKILL/restart proof after SAVE and before SQL
publication commit. They have no supported production selection wrapper. They do
not transfer full B0 tasks, substitute for a complete PG-derived B0 transfer
manifest, attest all-writer host quiescence, or implement full cold reversal.

Migration `0038` adds the durable joint ordinary/B0 transition journal. Internal
native `BeginColdOwnershipTransition` commits one canonical intent binding the
prepared cohort, prior ordinary/B0 identities and exact active/target/rollback
release and host-cold evidence digests before allocating an epoch. A digest is
an integrity binding; the supported ADR006 host wrapper must independently
verify those releases and attest every writer stopped under its mutation lock.
Protected native commands expose exact intent/reservation/publication/activation;
the all-writer host wrapper and production selection path remain to implement.

`ReserveColdOwnershipEpoch` revalidates that exact intent and fresh canonical/
Redis cohort, allocates one new shared epoch, retires the old ordinary owner,
stages its replacement and commits the reservation together. PostgreSQL sequences
can advance through rollback. An interrupted allocation retains pending intent;
recovery allocates another fresh epoch rather than adopting the burned high-water.
An uncertain successful commit is inspected by exact intent/source and returns
its same reserved plan after fresh readback. B0-only allocation now refuses any
unfinished joint journal before `nextval`, including when ordinary ownership has
already retired. History and immutable identity survive application rollback.

Real private database tests prove initial/replacement reservation, retirement,
barrier ordering, configuration/source rejection before allocation, a forced SQL
failure after sequence advance/retirement, independent-connection recovery and
uncertain-commit retry. Full canonical rows, deadlines, receipts and Redis state
remain unchanged. Subsequent publication fixtures and the protected native
executable now prove joint routing, activation and actual coordinator-process
interruption/recovery. Host/container quiescence and full cold reversal still need
implementation and proof before selecting ordinary Go.

`Authority.WriteGreenhouseRichBatch` now persists one prepared 1–500 posting
chunk through installed ownership and exact Redis/PG attempt authority. It uses
the frozen Python URL diff/insert/description statements, ordered global posting
locks, first-owner foreign liveness/relist rules, rich replacement/NULL refresh
rules and exact-byte R2 deduplication. Inserts, diff effects and descriptions are
atomic within the native chunk; a SQL or staged-hash failure rolls them back.
Database deadlocks alone receive the existing three-attempt jitter budget.
A fresh publisher reservation is rejected under the canonical board lock.
Only a monotonic heartbeat extension can survive that rollback. Native rich
preparation in the executor separately matches the actual Python rich writer,
including missing titles and default locales. Maintenance consumers import only
the queue's detached nullable persistence values, without enrichment dependencies.

The standalone chunk API remains nonterminal. `BeginGreenhouseCycle` now binds
the PostgreSQL discovery start to one owned claim; `WriteRichBatch` accounts for
only committed chunks and poisons absence finalization after a failed chunk.
`FinishSuccess` applies the Python disappearance, repeated-empty, partial and
confirmed-contraction policies using fresh PostgreSQL metadata. `FinishFailure`
retains the five-strike recovery quarantine and daily-capped backoff. Terminal
effects and the canonical deadline commit with the opaque attempt receipt;
`Settle` acknowledges that same durable deadline. Malformed lifecycle metadata
fails closed. The detached Redis claim snapshot is preserved through settlement.

`FinishProviderGone` accepts only the exact Greenhouse token API's HTTP 404,
with spaced confirmations, daily confirmed-gone probes and nonempty recovery.
`FinishGreenhouseReservation` handles pre-existing reservations without a fetch,
or a new exact-resource header signal; it preserves visibility, prior success and
failure accounting. The native skip persists the future canonical deadline,
closing Python's Redis-only skip scheduling gap. No missing header clears a
reservation. This component does not implement the network transport or claim
complete publisher-policy coverage.

CI regenerates 21 disappearance and 20 provider-gone cases from the actual
Python processor/policy, alongside the nine frozen lifecycle SQL statements.
Real database/queue tests cover repeated cycles with stale Redis metadata,
transaction rollback retaining earlier chunks, and terminal commit-before-ack
recovery in changed lifecycle states. Full inventory normalization/completeness,
the installed executable, transport/circuit behavior and actual process fault/
cold-reversal evidence remain required. No production owner is selected here.

The offline `InspectGreenhouseMonitor` boundary observes the standard explicit
token/skip profile on the existing board hash. It validates canonical board and
company IDs, provider URL, both browser flags, intervals and unchanged shared
throttle identity, rejecting unknown/filter/proxy/enrich/alternate-token settings.
Empty or null skip options have no extraction effect. It makes no request or
queue mutation and cannot establish enabled status, profile ownership or write
authority. Its effective digest excludes runtime lifecycle/learned-egress
observations; its exact snapshot digest still binds all of them. Native
processing must freshly validate and preserve their policy before effects.

`Authority.ObserveGreenhouseMonitor` joins that observation to the actual
enabled, recoverable PostgreSQL board while holding the existing routing epoch
barrier and a shared canonical row lock. It compares company/source/interval/
throttle/browser/effective metadata settings to the current Redis hash and
returns no profile on a stale projection, unsupported state or retired epoch.
It neither claims work nor installs a fence. Enabled suspect/quarantined/
gone_pending/gone rows remain eligible for their installed native owner so
ordinary recovery cannot strand selected members excluded from legacy. Actual ready
route membership, active-plan ownership and fresh canonical state still must be
checked at selection; this observation grants no later authority.

`NewHostCircuits` freezes trusted startup settings on the same ordinary Redis
client. Its failure/success scripts are byte-identical to the production Python
scripts. `PreflightGreenhouseHost` checks the installed claim before discovery:
learned failure host first, configured board hostname otherwise. Open circuits
and occupied half-open probe leases produce committed PostgreSQL deferrals and
opaque receipts without spending the board failure budget. Redis circuit trouble
fails open with bounded diagnostics; it cannot override attempt authority.

`FinishFailureWithHostCircuit` advances the shared host once per actual run,
caches ambiguous outcomes across SQL retries, and commits the circuit lower
bound alongside normal board backoff. Deadlines round upward to PostgreSQL
microsecond precision. Migration0037 retains the learned failure host with the
terminal receipt. Token-guarded rescheduling publishes it atomically after lease
retirement so the inflight snapshot stays unchanged and future claims see the
new routing. Commit-before-ack recovery retains that host without another
circuit increment. Stale leases, changed receipt host or retired epochs reject
publication. `RecordGreenhouseHostSuccess` requires the native success receipt,
resets actual observed hosts and never replays completed protective updates.
Provider404 and publisher outcomes remain separate from generic host failures.
Final resource adapters bind their initial token endpoint to the installed claim
while retaining the completed response resource as evidence. The claim runner
uses its own sealed verified fetch; these URLs authorize no extra retrieval.
Rescheduling validates all queue/index types and numerical inputs before writes,
so a corrupt ready index cannot partially retire the lease or lose host recovery.
The native worker executable must integrate this prepared path before selection.

The durable document and native/legacy selection library are prepared. An
active document or a passing source fixture is insufficient to select a
production owner. Install and prove the exact processing executable, both
owners' startup identities and supported quiesced activation/reversal first.

1. Integrate this selection into the native processing executable and supported
   all-writer activation/reversal. Prove installed exclusivity and preserve
   producer/sync/repair/deferred/never-successful behavior. Coordinate the shared
   B0 epoch and exact startup/projection identities for every writer.
2. Connect the first proven native HTTP/API family to monitor and detail
   processing. Greenhouse is the leading census candidate, pending effective
   configuration validation. Preserve inventory completeness/truncation,
   disappearance, source identity, native enrichment, description dedup/R2,
   retry/circuit/publisher policy, disabled/deleted and never-rescrape semantics.
3. Prove actual process cancellation/crash, commit-before-ack recovery and
   supported cold reversal, then canonical output, freshness and queue
   conservation using exact immutable candidate images.
4. Merge/deploy only after required CI and the actual Crawler Deploy Gate pass
   with fresh head/base/hold/ownership checks. Expand profile coverage until every
   enabled board has a proven route and all ordinary scheduling/maintenance
   consumers run natively.
5. Compare whole-service CPU/RAM/density/attributable cost, pass the rollback
   window, and retire production Python, Playwright, Chromium and runtime-only
   assets. Preserve useful isolated offline Python tooling.

The full migration goal stays active through these gates. A deployed B0 cohort,
this authority library, or a selected first HTTP family is only a checkpoint.

## Native joint runtime admission

Migration 0040 retains immutable canonical B0 targets alongside joint history and
binds each reserved plan to one journal. Preparation commits the exact target
with publishing phase; later missing targets cause containment. Schema transitions
into publishing/published/active require that target; downgrade refuses retained
journal or target history. Private fixtures alone may truncate their owned tables.

`OpenJointOwnedAuthority` requires the exact approved ordinary plan/source/epoch
and separately installed bytes of the reviewed actual B0 Lua. The matching active
journal binds the target hash; its retained canonical payload and fresh PG/Redis
configurations are re-attested. `OpenOwnedAuthority` cannot ignore a journalled
plan or an unfinished joint transition. Unselected claimants also reject open
joint intent, even before any ordinary plan becomes active.

Each operation verifies the immutable binding under the existing lease/epoch
barriers. One read-only EVAL combines the actual B0 conservation audit with fixed
producer selectors and exact permanent projection/joint witness/route/owner.
Healthy live B0 inflight/dead/terminal work is allowed; this is not a quiescence
attestation. Current claim checks retain the bound audit through end-of-write,
host circuits, heartbeat and settlement. Lost/mismatched/expired evidence grants
no pop/write/lease retirement and is never repaired from a latest selector.

Real private tests cover 15 authority faults before pop, conserved actual B0
inflight alongside ordinary execution, and witness loss before heartbeat/write/
settlement of a committed future-due receipt. The real native executable also
admits exact joint startup and exits without queue/canonical changes after witness
loss. This proves the native component; legacy joint admission, the supported
host wrapper, complete transfer/full reversal and fleet admission remain required.

## Retained cold retirement

Migration 0041 and `BeginColdOwnershipReversal` retain an immutable exact reversal
intent and set the forward journal to reversing before sequence allocation.
The document binds the source intent/revision/epoch/plan/phase, rollback release,
previous ordinary plan/B0 receipt and independently attested host-cold digest.
Retirement does not require a candidate to remain enabled or its Redis witnesses
to survive. `ReserveColdReversalEpoch` allocates a fresh epoch and atomically
retires the active ordinary owner; a failed SQL commit burns the sequence while
retaining pending intent, so recovery allocates another fresh epoch. Exact
reserved retries require the recorded epoch and no active ordinary owner.

`InspectColdReversal` reads retained immutable progress without mutation/epoch
barriers or live allocator reads. It can observe pending history while an
allocation transaction is paused. Real PostgreSQL/Redis tests cover all four
source phases, changed/disabled configurations, missing witnesses and complete
private Redis loss, wrong rollback bindings, barrier contention and actual
executable SIGKILL before retirement commit. Canonical receipts/future deadlines
and every Redis value/expiry class remain unchanged through recovery.

This is retirement, not restoration. The journal remains reversing, supported
claims remain blocked, and the schema refuses a reversed phase without completed
restoration evidence. The supported ADR006 host wrapper, PG-derived B0 transfer,
durable cross-store restoration and complete prior-release readiness remain to
implement and prove before any production ordinary owner is selected.

## Native PostgreSQL-derived B0 rollback preparation

`BuildColdB0RollbackPlan` now prepares an opaque deterministic rollback manifest
from exact retained reversal/source/retirement identities and an explicit source
queue epoch. The source-pinned actual B0 audit and a read-only atomic observation
require permanent route/record/producer/guard evidence, exact fixed selectors,
conserved ready/dead/terminal records and no inflight work. A second observation
must match while canonical rows remain locked under the shared mutation barriers.
No allocator high-water or latest route is adopted.

PostgreSQL supplies current URL/domain/board/hash/interval and eligibility.
Ready records preserve their original transfer kind and exact decimal score;
dead/terminal records use canonical future due time and current parser lane.
Disabled, gone, inactive, missing and unscheduled rows drop. Historical Go SQL
fence IDs remain in the manifest; live DB leases or matching legacy inflight/
dead-letter suffix authority reject. Changed candidate configuration and lost
ordinary joint witnesses do not prevent preparation; complete Redis task-authority
loss remains contained. Exact bigint hashes, microsecond due times and first-time
intent survive without fetch/parser/enrichment/database callback replay.

Observation is bounded to 2048 records/fence IDs, 32 MiB record/manifest bytes,
65536 entries per legacy authority index, and 16 KiB canonical source URLs.
Exceeding a bound rejects before effects; it does not discard enabled work.
The plan binds the caller's source receipt digest, but independent receipt/
sentinel/release/host verification belongs to the supported ADR006 wrapper.

Real private tests feed the native manifest to the unchanged actual rollback Lua
for ready/dead/terminal work and changed/disabled/deleted configurations. These
fixtures prove compatible restored schedule/config/drop effects while leaving
canonical rows/receipts/future deadlines and the reversing journal untouched.
The production API prepares only: durable plan retention before Redis mutation,
SAVE/readback/tombstone recovery, exact historical fence cleanup, sentinel/receipt
recovery, restored ordinary/B0 ownership and full release readiness remain to
implement and prove. No protected restore command or production owner is selected.
