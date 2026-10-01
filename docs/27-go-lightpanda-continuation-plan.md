# Go and Lightpanda migration continuation plan

Latest native client source `8d2874e6b0bd311c9deb491163cd50cdc2f3b46d` adds the
authenticated Go producer control client and shared installed wire types.
Manifest, prepare, approved activation and enqueue use the fixed UID-10001 socket,
Linux kernel peer credentials, exact canonical bounded framing and one attempt
per mutation. The actual installed producer/private Redis fixture now exercises
this client, including exact operator retry, wrong-digest rejection and terminal
reactivation. Actual Python wire captures and private socket fault/deadline race
tests pass locally; both Linux architectures compile the installed fixture.
Linux native socket/capture steps passed on both architectures in 36919506114.
The actual UID-10001 producer/private Redis test and hard restart passed in
full-CI job 110563817514 at `94979ef6c`: exact approved operator retry,
wrong-digest rejection, occupancy conservation and terminal reactivation.
This verifies the client component; full current-source admission remains required. See
[native client evidence](evidence/go-b0-native-producer-client-2026-10-01.json).

Restoration checkpoint `387ccaa56` passed Linux PG17/Redis on both architectures
and full CI. Installed-image ARM64 passed; AMD64 conservation failed, so that
checkpoint is not admitted. Diagnostic source `55adcb527` reproduced a failure
at a later conservation seam and exposed a separate metrics-observation race:
database completion/Redis ACK precede outcome accounting and claim release.
The fixture now waits for the whole settled metrics contract within the same
bounded readiness window. Both conservation seams separately identify Redis
and canonical effects. Linux 36919506114 identified the changed key as the real
source claim's three-second `ratelimit:jobs.example.test` bucket. The fixture
now verifies that actual expiry, then makes only the private source bucket
permanent before all conservation baselines. Its exact value remains included
in strict snapshots; no production rate-limit behavior changes. Three local
repetitions of both actual executable fixtures passed after this fix (26.846s).
Fresh Linux and installed checks must resolve the finding. Initial restoration checkpoint
`8bdb17832` whole B0 36913625698 has now passed; this is historical source evidence.

Latest native runtime source `9d252deccfbe031ea1c4dbffee05996261b68db2` adds durable B0 restoration and
exact historical fence cleanup through migration 0042. Approved manifest bytes
commit before Redis effects. Fresh PG-derived plan and atomic source-pinned actual
queue observations precede the unchanged rollback Lua; acknowledged SAVE and exact
permanent tombstone readback precede SQL progress. A subsequent transaction clears
only retained Go source-epoch/shard fence IDs. Exact interrupted retries preserve
canonical rows, ordinary receipts and microsecond future deadlines.

Four protected source-bound commands expose preview, retention, restoration and
read-only progress inspection. The actual native executable survives SIGKILL after
SAVE/readback before SQL progress; independent inspection works while paused,
then exact restart and repeated cleanup reach fences-cleared at the same epoch.
Full queue/worker/exporter races passed (45.589s/31.263s/4.891s), all 295 mandatory
legacy tests passed without skips, migration downgrade-0041/re-upgrade and 37
repository checks passed. See
[durable restoration evidence](evidence/go-ordinary-b0-restoration-2026-10-01.json).

Preceding planner checkpoint `de9fc4f52` passed Linux PG17/Redis 36908705159,
full CI 36908788510 and both installed images 36908792798. Both downloaded artifacts
verify all four actual executable fixtures, exact source/image/binary and matching
34 asset hashes. Whole B0 36908705415 also passed, completing all four workflows. Earlier retirement checkpoint
`286810cfc` has now passed all four workflows, including whole B0 36905777358.
The actual deploy gate refuses draft. Current-source checks remain required.

Final fixture source `37c0bd723d980be6df845af625f55cc03edba466` compares exact logical Redis bytes,
types, scores and expiry classes. Linux AMD64 on the initial restoration checkpoint
reported opaque combined DUMP/canonical mismatches at different read-only seams;
ARM64 passed. DUMP embeds internal encoding/iteration order, so the fixture now
uses stable logical observations, separates canonical diagnostics and proves real
value/type/microsecond-score/expiry/non-UTF8 drift detection. Full corrected worker
races passed (30.722s). Fresh exact-source Linux and image checks must resolve the
CI finding before admission; no production runtime was changed by this correction.

Next complete fresh ordinary/B0 restored ownership, sentinel/host receipt recovery
and full release readiness through the supported ADR006 all-writer host wrapper.
Finish forward PG-derived B0 transfer and remaining actual SIGKILL seams. The
completed B0 restoration deliberately leaves the forward journal reversing,
ordinary projection/joint witness in place and ordinary claims blocked. Every
enabled effective profile/runtime consumer, canonical/publisher/freshness/queue
parity, comparable whole-service resource/cost, exact-gated rollout, actual rollback
window and Python/Playwright/Chromium retirement remain required by the active full
migration goal. Preserve useful isolated offline Python tooling.

Preceding runtime `a7502f1bb87a80a2acb8df818080b4f1acee6ae8` introduced the
canonical PG-derived B0 rollback manifest and its bounded actual-Lua preparation
proof. Keep its
[preparation evidence](evidence/go-ordinary-b0-rollback-preparation-2026-10-01.json)
as the historical preparation checkpoint.

Current verified joint checkpoint, October 1: `f2b82c73d` passed Linux AMD64/ARM64
PostgreSQL/Redis (36899324776), full CI (36899366997), both installed images
(36899371310) and whole B0 (36899324501). Both downloaded artifacts bind exact
source/image/binary and all four executable fixtures with matching 34 asset
hashes. Dormant renderer 36899324562 passed build-only; publish/deploy skipped.
The actual deploy gate refuses draft. These results do not admit later source.

Previous runtime source `c0f46f848db6862f185a548b183a87b615aeb6eb` adds migration 0041 and three protected
native reversal commands: begin, reserve and retained-history inspection. Exact
reversal intent commits before allocation, contains the forward owner and binds
source/rollback identities. Fresh retirement survives disabled/changed candidates
and lost Redis evidence. Actual SIGKILL after nextval/owner retirement leaves SQL
rolled back and intent pending; recovery allocates another fresh epoch. Independent
read-only inspection observes pending state while allocation is paused. Canonical
rows, receipts, future deadlines and Redis values/expiry classes remain unchanged.
Full private queue/worker races and 295 legacy tests passed; migration downgrade/
re-upgrade and 132 repository checks passed. See
[retirement evidence](evidence/go-ordinary-cold-retirement-2026-10-01.json).
Claims remain blocked: retirement does not complete restoration or host readiness.
Current-source Linux/full CI/both installed-image/B0 checks are required after push.

Previous runtime source `aab47b5e8857e6521a7bfb38f8cc174391988e52` completes legacy
ordinary startup/claim joint admission. Every claim, including an unselected
worker, holds existing DB lease/epoch barriers and refuses unfinished joint
intent. A journalled owner requires protected installed actual B0 Lua and exact
active journal/source/epoch/plan, retained target, fresh PG/Redis B0 configuration,
fixed selectors, conservation audit and permanent shared projection/witness/route/
producer identity. No latest owner or lost witness is adopted or repaired.

Go and Python now hash exact numeric values across JSONB/Redis spellings without
float rounding. Fifteen shared canonical cases and fourteen invalid inputs cover
precision, decimal/scientific spelling, negative zero, HTML/Unicode escaping,
duplicates, depth and bounded normalized exponents. New target hashes require
fresh capture/approval before intent. The actual native coordinator publishes the
journal/target consumed by a real Python startup/claim probe; nine faults reject
both paths without Redis value/expiry-class or canonical changes. Real B0 inflight
work remains compatible. The pipeline's unselected loop cannot pop during intent.
See [legacy joint evidence](evidence/go-ordinary-legacy-joint-admission-2026-10-01.json).
Fresh exact pushed-source Linux/full CI/both installed-image/B0 checks are required.

Native runtime source `d1a2316f0d1662e06aa95f6924c3a09840ec3ae2` and migration
0040 previously completed immutable target/journal bindings and native fresh
startup/claim/write/heartbeat/circuit/settlement admission. Fifteen real faults,
healthy B0 inflight and fencing a completed future-due ordinary receipt are proven.
Legacy writes/settlement still rely on the supported host draining/stopping all
writers before ownership changes; the new guard specifically covers admission.
The native [joint evidence](evidence/go-ordinary-joint-admission-2026-10-01.json)
remains its historical component proof.

Coordinator runtime source `6f0ceca14fed1ec34ed92ba289a3d5173cb1a6ce` exposes seven
protected compiled-source-bound coordinator commands: B0 target capture, exact
canonical intent, reservation, retained-history inspection, preparation, publication
and activation. Exact
bounded non-symlink files bind digests and source. Begin/reserve require intent's
previous epoch; prepare/publish/activate require the exact reserved epoch/plan.
A real executable SIGKILL after MSET/SAVE/readback and before SQL publication
commit leaves publishing intent and staged plan. Exact restart/retries recover
without replaying canonical or other Redis effects. Missing witnesses remain
contained. Full worker races passed (27.723s); 126 repository checks and vet passed.
See [coordinator evidence](evidence/go-ordinary-cold-coordinator-2026-10-01.json).
Fresh pushed-source Linux/full CI/both installed-image/B0 checks remain required.

Publication runtime source `28d155566b50297b65933757294d5ff8bfb442d3` adds native
shared-epoch publication/activation through migration 0039. A fresh canonical/
Redis B0 target binds fixed selectors and exact configuration hashes. Redis
pending witness precedes committed `publishing`; only then can the unmodified,
SHA256-pinned actual B0 conservation audit and prior-byte CAS publish ordinary
projection plus joint witness in one MSET. SAVE acknowledgement and exact atomic
readback precede `published`; exact ordinary DB owner and journal become active
in one subsequent transaction. Missing witnesses cause containment, not repair.
A successor binds the exact active generation and refuses an old B0 epoch.

Real private full queue/worker races passed (30.145s/43.047s), 290 mandatory
legacy tests passed without skips, final publication races passed (7.400s), and
130 repository checks plus vet/ruff/pyright passed. SAVE denial, post-SAVE SQL
failure and activation rollback/retry conserve canonical rows/deadlines/receipts
and every other Redis key. A no-save Redis shutdown and fresh process load prove
durable RDB witness/projection/B0 records/guards. See
[publication evidence](evidence/go-ordinary-cold-publication-2026-10-01.json).
Fresh pushed-source Linux/full CI/installed-image/B0 checks remain required.

Protected commands now expose the completed native primitives; no supported
production selection wrapper exists yet. They do not replace complete PG-derived
B0 task-transfer/sentinel/receipt evidence or attest all-writer host quiescence,
release/rollback identities and readiness. Next complete restoration using the retained retirement intent/epoch and the
supported host wrapper; prove all remaining interruption seams and full cold reversal (including changed/disabled cohorts).
Do not redo completed staging, worker process/crash, intent/reservation,
publication/activation, protected coordinator commands or completed native/legacy
joint admission guards or completed cold-retirement intent/SIGKILL recovery.
Production ordinary remains Python and the full migration goal is active.
Running B0 and installed-image measurements finish while the newest pending
candidate waits; each source needs its own report and admission.

The next delivery sequence is:

1. Preserve the completed Linux image binary/asset tests, retaining exact source,
   image ID, binary/asset hashes and result logs. Prove the deployed container's
   effective settings and full public fetch/processing before selecting a cohort.
   The no-fetch crash fixture does not supply public TLS/profile admission.
2. Complete the coordinated ownership protocol described below, then prove its real
   all-writer cutover, interrupted-publication containment and cold reversal on
   private PostgreSQL/Redis and installed containers before production selection.
3. Promote the queue foundation and ordinary runtime through current exact-head
   Required CI and Crawler Deploy Gate. Recheck source/base/draft/holds/operator
   state immediately before each merge; rebuild/retest after any changed head.
4. Refresh the canonical census of every enabled effective configuration. Admit
   strict Greenhouse first; extend the assembled worker to the remaining native
   HTTP/API monitors, detail extraction and Lightpanda profiles in bounded slices.
   Preserve enabled suspect/quarantined/gone_pending/gone rows and unsupported
   configurations in the remaining owner until their replacements are proven.
5. Replace remaining runtime scheduling, reconciliation, maintenance, publication
   and deployment consumers. For each slice, compare actual canonical output,
   database effects, publisher policy, due times and queue/freshness conservation.
6. Run comparable whole-service output and CPU/RAM/density/attributable-cost
   measurements with recorded images, settings, inputs and windows. Exercise the
   supported full cold reversal and complete the actual rollback window. Remove
   production Python, Playwright, Chromium and legacy runtime-only assets only
   after replacement coverage and operational authority are established; retain
   useful isolated offline Python tooling.

## Coordinated ordinary and B0 ownership protocol to complete

Ordinary ownership and B0 use the same PostgreSQL routing-epoch allocator. An
ordinary transition cannot advance it while B0 continues claiming, and a later
B0-only reversal cannot silently retire an active ordinary owner. Extend the
supported deployment/ownership protocol under ADR006; the existing B0 active
receipt intentionally prevents an ordinary image release until cold reversal.
Do not bypass that receipt or patch a running environment to activate Go.
The prepared B0-only allocator now refuses an active ordinary plan before burning
a new epoch, taking the ordinary lease barrier before its epoch barrier. Real
PostgreSQL proves preserved owner/sequence and allocation after retirement; legacy
absent-schema behavior remains compatible. Fresh deployed-source checks are required.

Native journal preparation/reservation now persists canonical intent before
`nextval`. It validates the prepared current-epoch cohort against fresh PG/Redis
state, retires an exact old ordinary owner, stages its replacement at a fresh
shared epoch and records reservation in one bounded transaction. A rolled-back
allocation can burn a sequence value: exact pending recovery allocates another
fresh epoch; it never adopts the high-water. Exact reserved retry rechecks the
current epoch and fresh staged cohort without reallocating. B0-only allocation
refuses unfinished journal phases, and rollback cannot delete retained history.
Protected native commands now expose these database primitives; input evidence
digests do not prove host quiescence or release identity. Publication and its
actual SIGKILL/restart seam are proven locally; remaining interruption seams,
supported all-writer selection and full cold reversal remain to implement/prove.

Shared publication now captures exact canonical/Redis B0 board configurations
and fixed producer selectors without adopting an epoch. Redis pending witness
commits before journal `publishing`. One EVAL uses the actual source-pinned B0
conservation audit to guard exact new shared route/selectors, conserved nonempty
records and zero inflight/dead work, then uses one MSET for ordinary projection
and joint witness. Acknowledged SAVE and exact readback precede `published`;
subsequent exact owner/active-journal installation is atomic. SQL/Save failures
retain inspectable recovery phases. Lost/expired/partial witnesses are contained.
An exact active predecessor can be retained as superseded; its release/plan/epoch
and prior projection/witness must match. A stale B0 route prevents publication.
Protected source-bound commands expose these primitives, with actual publication,
retirement and B0 restoration interruption/recovery proof. They still require the
supported host wrapper, complete forward B0 task transfer and full cold reversal
before selecting production ownership.

Bind one protected transition to the exact active and candidate release generation,
immutable crawler/browser/renderer image identities, installed binary sources,
canonical data/runtime contracts, ordinary cohort configuration digests and B0
selectors. Native `--stage-ownership`/`--inspect-ownership` now prepare and inspect an exact
staged ordinary plan. They require protected modes, compiled source and explicit
current epoch; staging cannot activate ownership or adopt an allocator epoch. A changed board or
projection invalidates the proposed transition and requires fresh preparation.

Under the host mutation lock, stop and attest every claimant/writer, exporter,
description drain and competing Compose one-off. Preserve complete rollback
spec/env/image/data/receipt evidence before mutation. Allocate one fresh epoch
while the lane is cold, stage and validate the new ordinary plan against fresh
canonical configurations, and install both ordinary and B0 ownership at that
same epoch. Record a durable pending transition before any cross-store mutation.
Only exact installed plan/epoch/source/projection readback and persisted Redis
state may advance the transition to active and release the complete stack.
Native and remaining Python workers must bind the same ordinary projection;
startup/config drift or lost projection must contain the lane.

Cold reversal must retire both old owners while all writers are stopped, allocate
a new epoch, restore the exact rollback release and data tree, rebuild its supported
queue projections and resync canonical future deadlines before any Python worker
restarts. Keep committed ordinary receipts and learned-host evidence recoverable;
never replay completed network/persistence work or invent an empty inventory.
Interrupted activation/reversal retains its journal/receipts and stopped writers
until deterministic recovery completes. Cover faults at database transition,
Redis publication/persistence, receipt publication, startup and readiness boundaries.
A failed reversal must leave the affected lane stopped, with its recovery authority
intact. Then verify one naturally due owned cycle, old-owner exclusion, posting/
description/upload continuity, due times and remaining-owner freshness.

### Remaining ownership and host work in dependency order

1. Replace Python forward activation planning/application with native PG-derived
   B0 task transfer at the exact retained shared epoch. Preserve existing producer
   UDS preparation/activation digests, actual Lua transfers, legacy schedule intent,
   completed terminal records and bounded lifetime capacity; refuse inflight/suffix
   authority. Retain a durable complete manifest before the first per-task effect,
   then prove partial transfer/SAVE/restart without replay or lost schedules.
2. Restore both supported rollback targets: a legacy ordinary owner and a prior
   native ordinary owner. The native target needs a fresh plan at the retained
   retirement epoch bound to the rollback binary's source and freshly validated
   effective configurations. Retired plans remain immutable. Existing native
   admission requires an exact active journal and B0 target/projection/witness;
   closing the old reversal without that replacement would leave it unowned.
   Design and prove the atomic history/owner completion before releasing claims.
3. Independently verify immutable active/target/rollback generation, complete data
   tree and runtime contract, spec present/absent manifest, env and image/binary
   identities. Under `/run/lock/jobseek-crawler-mutation.lock`, stop and attest
   ordinary/browser/native claimants, producer, exporter, drain, maintenance and
   competing one-offs. Integrate the protected native commands with the supported
   ADR006 host workflow; preserve the existing active B0 receipt as recovery authority.
4. Complete Go-owned sentinel and durable host receipt recovery, acknowledged
   Redis persistence and exact readback, final fresh database/queue ownership and
   full-stack readiness. Actual SIGKILL must cover historical fence cleanup before
   commit, sentinel/receipt publication, release selection and readiness failure.
   Any failed restore leaves every writer stopped and the exact recovery record intact.
5. Prove installed container/public-fetch settings and a naturally due owned cycle,
   then promote through fresh exact-head gates. Refresh all enabled effective
   profiles and port remaining monitor/detail/browser and runtime consumers with
   canonical/publisher/freshness/queue conservation. Complete comparable whole-service
   resource/cost admission and the actual rollback window before runtime retirement.

## Historical delivery record

Reviewed 2026-09-30 against `origin/main`
`c571568167b7abf7dfa80a7f9c73bcbb29d49cb2` and current GitHub PR/check state.
This is the forward plan for completing the crawler service migration. Start
with the saved salary candidate, then remove the Python orchestration and
persistence boundaries while completing coverage of enabled boards. Preserve
Python where it remains useful outside the production crawler runtime.

Current production is recorded in the [September 30 location checkpoint](28-go-location-resolver-checkpoint-2026-09-30.md).
Native executor implementation and remaining ownership gates are recorded in the
[September 30 native B0 candidate checkpoint](29-native-go-b0-executor-checkpoint-2026-09-30.md).
The initial review below is historical; use newer verified operational evidence first.

Current delivery: shared salary/location and native B0 persistence are promoted
at crawler v0.13.902, revision `b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`.
Native B0 PR #10204 passed final 16-arm admission and exact-head required gates,
completed coordinated crawler/renderer releases, and now runs Go at epoch 145.
Supported native cold reversal/restoration (143→144→145) and one naturally due
Bunq fenced commit with exact stored hash/upload/schedule continuity are proven.
The [native checkpoint](29-native-go-b0-executor-checkpoint-2026-09-30.md) binds
immutable images, observations and limits. Whole-service cost and fleet output
are still incomplete. The independent ordinary queue foundation is saved in
draft [PR #10207](https://github.com/colophon-group/jobseek/pull/10207), with real
Linux AMD64/ARM64 Redis contracts passed. It now also prepares native
PostgreSQL attempt authority, guarded reaping and canonical deadline recovery.
At head `86813918a`, Required CI, installed runtime contracts, both Linux
PostgreSQL/Redis architectures and all 16 synthetic B0 admission arms passed.
Stacked draft [PR #10210](https://github.com/colophon-group/jobseek/pull/10210)
adds strict Greenhouse eligibility and canonical PostgreSQL/Redis observation;
implementation head `e5ecd5d29` passed both real Linux database/queue jobs.
Its subsequent `6a71c4c99` ownership implementation adds immutable durable
staged/active/retired cohorts and exact active readback; both Linux PostgreSQL17/
Redis architectures passed run 36790040524. Subsequent `f7ccc7b06` binds native
and legacy claims to the exact active plan, canonical configuration and atomic
before-pop selection; both Linux architectures passed run 36793700617 with
mandatory real legacy PostgreSQL/Redis attestation tests. Rich processing source
`8a5834e61` now adds native content preparation against 18 captured Python cases
and owned atomic 500-posting insert/touch/relist/description batches; both Linux
architectures passed run 36796933484. Follow-up `449e58052` rechecks the current
board publisher reservation under the canonical write lock and passed local
real database/queue races. Checkpoint `01b7366f5` subsequently passed both Linux
architectures in run 36797683763 and full CI in run 36797708877; its B0 admission
run 36797683889 was still measuring when the next implementation was saved.
Those results do not cover later source or grant merge authority for a draft.

Latest implementation `8a6f1bc08` binds owned Greenhouse batches to a terminal
board cycle. It applies disappearance/empty/partial/confirmed-contraction,
quarantine/backoff, provider-404 and publisher-reservation policies under the
same PostgreSQL attempt authority and commits the canonical due time with the
receipt. Enabled suspect/quarantined/gone_pending/gone states retain their
installed native owner; fresh PostgreSQL metadata avoids stale Redis baseline
and confirmation state. Local real database/queue proof includes 41 actual
Python decision cases, 10 lifecycle integration groups and commit-before-ack
recovery in changed board states. See the
[portable lifecycle evidence](evidence/go-ordinary-lifecycle-2026-10-01.json).
Checkpoint `7e1094733` passed both Linux architectures in run 36800629362,
full CI in run 36800624392 and B0 admission in run 36800629213. The actual
deploy gate still rejects the draft; earlier-head greens grant no later-head
merge authority. Production ordinary workers remain Python.

Processing assembly `9d7a8f5a8` adds the
[native ordinary package](../apps/crawler/go/ordinary-worker/README.md).
Forty URL and six full-inventory captures match the actual default Python rich
monitor's dictionary, sanity/canonicalization and alias content rules, including
interleaved duplicates. Native normalization keeps every collected job above
the 50,000-job flag. A real owned PostgreSQL/Redis fixture now connects native
models, lookup/currency snapshots, SQLite locations, three 500/500/1 posting
batches, terminal disappearance and canonical receipt settlement for 1,001
postings. Preparation failure after a committed prefix invalidates later
success/absence authority. Checkpoint `654ed153e` passed full CI in run
36831279197 and B0 admission in run 36831284162. Linux run 36831284128 passed
ARM64, but AMD64 first timed out fetching dependencies and then rejected a
different dictionary ordering in the saved Python drop-count capture. Runtime
counts matched; the next source makes capture serialization deterministic.
See [portable pipeline evidence](evidence/go-ordinary-pipeline-2026-10-01.json).

Latest source `aa99863e3` connects native Greenhouse discovery to the real
1,001-posting owned fixture through one HTTP 202 response. It preserves the
Python monitor's successful 2xx statuses, final-resource publisher headers
before provider/status/body checks, redirects and JSON byte encoding behavior
against 52 freshly captured Python cases. Failed reads and malformed inventories
never expose partial jobs. A real redirect fixture checks process-owned cookies
and client reuse. Ordinary taxonomy/currency/location lookups now use a separate
read-only one-connection reader with their own attribution. Local full native
assembly races (15.394 seconds), native reader/preparation races (3.069 seconds),
39 runtime/version/documentation checks and linters passed; all three oracles
were stable across three Python hash seeds. Production remains unchanged. See
[portable discovery evidence](evidence/go-ordinary-discovery-2026-10-01.json).
See the
[ordinary authority checkpoint](30-native-ordinary-authority-checkpoint-2026-09-30.md).
It selects no ordinary worker in production. Discovery checkpoint `c249e1742`
passed both Linux PostgreSQL/Redis architectures (36835150216), full CI
(36835149192) and B0 admission (36835150489). Its actual deploy gate remains
failed while draft; earlier-head results grant no later-head authority.

Latest runtime source `593a7c902` prepares a persistent verified native HTTP/1.1
client with an explicit CA bundle, process-owned cookies, 100 connections,
20 keepalive connections, five-second expiry, 20 redirects and separate
30-second operation deadlines. Every public hostname request rechecks the
Python address policy, including redirects and reused connections; new public
dials pin validated addresses. Native metering preserves request/response/
no-response conservation and encoded bytes. Hidden Go request retries are
suppressed. Gzip/deflate decoding matches 25 actual Python HTTPX captures;
233 address and nine DNS answer cases match the Python SSRF policy.
Verified TLS now feeds the real 1,001-posting owned database/queue assembly.
Local assembly races (24.672 seconds), reader/preparation races (3.357 seconds),
39 repository checks and linters passed; capture regeneration is byte-identical
across three hash seeds, and private fixtures shut down. See the
[portable direct transport evidence](evidence/go-ordinary-direct-http-2026-10-01.json).
This is prepared transport; ordinary production workers remain Python.

Transport checkpoint `8001134bc` passed both Linux architectures (36842233353),
full CI (36842225559) and B0 admission (36842233274). Its actual deploy gate
still rejects the draft; these checks do not cover the later circuit source.

Latest runtime source `8a84d82ca` prepares the shared host circuit path on the
existing Redis keys and byte-identical production Lua. Native preflight uses
the learned failure host, then configured board hostname; open and occupied
half-open circuits commit future PostgreSQL deferrals with opaque receipts.
Failure finalization records one protective circuit outcome per actual run and
commits its lower bound alongside normal backoff. Migration0037 retains the
learned host with the terminal receipt, and token-guarded Redis settlement
publishes it atomically after lease retirement. Commit-before-ack recovery keeps
that host and deadline without a second circuit increment. The inflight claim
snapshot remains unchanged. Circuit trouble fails open; fresh attempt authority
still guards every canonical effect.

Real migrated PostgreSQL/private Redis queue races (17.140 seconds), native
assembly races (22.135 seconds), reader/preparation races (3.276 seconds), 185
mandatory legacy tests without skips and 39 repository checks passed. Migration
head/down0036/head and fixture cleanup passed. The TLS 1,001-posting fixture
recovers its observed API circuit; a failed preparation fixture publishes its
fallback host at settlement. See the
[portable host circuit evidence](evidence/go-ordinary-host-circuit-2026-10-01.json).
This prepared library still selects no production ordinary worker.

Circuit checkpoint `3684247cc` passed both Linux architectures (36846132716),
full CI (36846125648) and B0 admission (36846132352). Its actual deploy gate
still rejects the draft; those results do not cover later source.

Latest runtime source `3e9aef091` connects the complete claim-bound Greenhouse
runner to a sealed verified transport. Initial token identity remains bound to
the owned claim while completed final-resource responses authorize redirected
provider404 and publisher policy. The runner connects preflight, native full
inventory/preparation/batches/lifecycle, host outcomes and receipt settlement.
It recovers committed receipts without HTTP, CPU or circuit replay. Concurrent
publisher reservation retains committed batches without spending failure budget
or absence authority. Redis settlement now validates all relevant index types
and numeric inputs before any mutations, preserving the lease and durable host
receipt when an index is corrupt.

Real TLS/PostgreSQL/Redis runner fixtures include the 1,001-posting assembly,
eight redirected policy/provider/error outcomes, no-fetch deferrals/reservations,
mid-body cancellation, reservation after a 500-posting prefix, true process-loss/
reap/reclaim recovery and thirteen atomic-settlement rejection cases. Full queue
races (26.414 seconds), assembly races (20.259 seconds), reader/preparation races
(2.875 seconds), 185 mandatory legacy tests without skips, 39 repository checks
and vet/format passed. Private fixtures shut down; production is unchanged. See
[portable claim-run evidence](evidence/go-ordinary-claim-run-2026-10-01.json).
Fresh candidate checks are required; this library selects no production worker.

Claim-run checkpoint `426a6c47d` passed both Linux architectures (36851878686),
full CI (36851882626) and B0 admission (36851878654). Its actual deploy gate
still rejects the draft; those results do not cover the later process source.

Latest runtime source `2b775782f` prepares `go-ordinary-worker`: exact compiled
source/plan/projection/epoch startup, pinned certifi CA and native model/reference/
location assets, five default active claims, lease renewal, task deadlines,
bounded signal drain/cancellation, claim-loop watchdog and stable bounded metrics.
Its identity-bound health probe opens no additional database pool and follows no
redirects. Docker slim/full wiring now embeds the checked-out source revision;
release, full CI, runtime-contract and B0 builders supply that exact argument.
No ordinary service is selected or started by this wiring.

A real native executable fixture completes and settles an owned no-fetch publisher
cycle, exposes identity/health/network/queue metrics, drains on signal and rejects
a wrong installed projection. Real TLS token loss cancels a blocked fetch without
canonical failure or terminal receipt. Race fixtures prove bounded claims, live
renewal during drain, own-acknowledgement serialization, deadlines/watchdogs and
uncooperative cancellation. Committed partial batches retain observed counts
without granting inventory completion. Full queue races (21.292 seconds), runtime/
assembly races (42.679 seconds), reader/preparation races (15.437 seconds), 185
mandatory legacy tests without skips, 96 deployment/image tests, 130 repository
checks, linters and both Linux cross-builds passed. These local wall times include
parallel build load and are not fleet performance/cost measurements. Private
fixtures shut down; production is unchanged. See
[portable ordinary process evidence](evidence/go-ordinary-runtime-2026-10-01.json).
Fresh installed-image/source checks remain required.

Next, prove the immutable installed Linux worker and process faults, including
SIGKILL after commit before acknowledgement and restart/recovery. Prepare supported
all-writer ownership/projection installation and shared B0 epoch cutover/cold
reversal before selecting a native ordinary cohort. Then complete every enabled
effective profile and runtime consumer, fleet output/freshness/queue conservation,
comparable whole-service CPU/RAM/density/cost and the rollback window before retiring
production Python/Playwright/Chromium. The full migration goal remains active.

This initial review made no production changes and did not reread the live hosts.
Production details below are the latest recorded checkpoint, corroborated by
the latest successful crawler deployment, rather than a new health attestation.
Refresh live release, ownership and selectors before any operational change.

## Initial checkpoint and source precedence

| Surface | Verified or recorded state | Resume from |
| --- | --- | --- |
| Saved implementation | Draft [PR #10177](https://github.com/colophon-group/jobseek/pull/10177), `fix-crawler/go-salary-extraction`, head `b6274d6c74bc96a219870cf4abb1b5d8bb1dcf3c`; candidate v0.13.899, not merged or deployed | [Salary handoff at the saved head](https://github.com/colophon-group/jobseek/blob/b6274d6c74bc96a219870cf4abb1b5d8bb1dcf3c/docs/24-go-lightpanda-salary-checkpoint-2026-09-28.md) |
| Candidate checks on 2026-09-30 | Required CI, Bindings and conformance, and Installed image parity succeeded; Crawler Deploy Gate failed intentionally while draft | Refresh checks after any base/head change; a successful gate reconciliation job is not a green Crawler Deploy Gate status |
| Last production release | v0.13.898, revision `20b4031ccc7e3056c6ca10b55b68bd5562c0c720`; [deployment 36467631537](https://github.com/colophon-group/jobseek/actions/runs/36467631537) completed including promotion | [Language checkpoint and production evidence](24-go-lightpanda-resumption-plan.md#production-checkpoint-2026-09-28--shared-go-language-detection-v013898) |
| Last recorded cohort | cdom active at epoch 133; 22 selected schedules, 17 already unqueued; 25 exact selectors staged; C2 Kandou dark | Live receipt and selector readback before mutation; use the current owner and epoch, not these saved values blindly |
| Last recorded renderer | September 28 nightly; source `fb2b1c865e90ccf67379aa1f303e2b5f5e8f95c0`; image `sha256:72b73991cc4a6820263970ccf07367b5d9193eb37cb9d742701fb78ea0104a21` | [Pinned manifest](../pilots/go-lightpanda/lightpanda-release.json); refresh official asset identity at resumption |

The salary handoff exists on the draft branch, not current main. Its immutable
GitHub link makes resumption independent of the previous local checkout. The
candidate records 2,664 compatibility cases across 18 currencies, 202 focused
Python tests, Go race/vet/module checks, and exact comparison of 512 stored
descriptions from 265 boards and 14 locales. These establish compatibility;
natural production salary output and database effects remain outstanding.

Use live committed release/receipt state first, then exact deployment and
deployed evidence, then saved candidate/check state. The September 24 opening
paragraphs in [#7935](https://github.com/colophon-group/jobseek/issues/7935) and
[#8648](https://github.com/colophon-group/jobseek/issues/8648) predate the
September 28 evidence. Their historical hold and ownership statements do not
establish current host state. No open `deployment-hold:crawler` issue was found
in this review; recheck immediately before any deployment and honor any new hold.

The original [migration design](23-go-lightpanda-migration.md) and the lower
historical sections of [the resumption log](24-go-lightpanda-resumption-plan.md)
remain references. The 10M-board projection, generic queue-v2 rewrite and
arbitrary sample/savings quotas are not prerequisites for the next slice.
Issue [#7966](https://github.com/colophon-group/jobseek/issues/7966) is
owner-closed; its retirement criteria remain useful, and closing it did not
establish completed migration. Keep it closed.

## What already moved and what remains

The deployed checkpoints record Go ownership of configuration/queue sync,
Typesense publication, backfill, reconciliation, schema and count refresh,
maintenance slices, and R2 drain. Several HTTP/API monitor and detail families,
shared JSON-LD/DOM parsing, classification, experience, HTML normalization and
language detection also run through Go. Reuse those implementations.

Go parsing behind a Python caller is an intermediate state. Current Compose
still starts three ordinary workers and the browser worker with `uv run`, while the active Lightpanda overlay now selects the Go database executor for
the admitted cohort. Shared location/salary processing is also Go. Python still
owns ordinary orchestration and portions of scheduling, failure handling and
persistence. Porting another parser alone does not retire
these processes. See [Compose](../apps/crawler/docker-compose.yml),
[activation overlay](../apps/crawler/lightpanda-b0-enabled.override.yml),
[CPU stages](../apps/crawler/src/processing/cpu.py), and
[shared Go enrichment](../apps/crawler/go/job-enrichment/README.md).

The September 23 retirement census counted 7,854 enabled boards, 102 configured
monitor values, 34 non-null scraper types and 524 distinct boards requiring a
browser. Those are historical coverage denominators. Refresh the registry and
effective routes now and before final cutover; do not use those counts as a
current completion percentage. Resolve implicit scraper metadata, rich-monitor
detail skips, and enabled suspect/gone/quarantined rows explicitly.

## Delivery sequence

Each row is a milestone containing small PRs. The proposed order prioritizes
the saved work and the boundaries that keep Python processes alive. Profile
coverage and offline replay can continue while a production cohort waits for
naturally due work. Production owner changes remain serialized.

| Order | Deliverable | Exit evidence | Existing tracking |
| --- | --- | --- | --- |
| 1 | Resume and promote shared Go salary extraction | Refreshed current-head checks, complete deployment/promotion, natural Go calls, exact salary/rate/DB results and cold reversal | [#10177](https://github.com/colophon-group/jobseek/pull/10177), [#7952](https://github.com/colophon-group/jobseek/issues/7952) |
| 2 | Move remaining shared location resolution and CPU processing into reusable Go packages | Identical leaf IDs/type ordering, locale/remote semantics, cache misses/backfill, raw metadata and content/hash behavior on captured inputs | [#7952](https://github.com/colophon-group/jobseek/issues/7952) |
| 3 | Replace the Lightpanda database executor with a Go implementation for the admitted cohort | End-to-end render/extract/enrich/commit through Go; fenced stale results rejected; queue, failure, due-time and transaction effects conserved through crash/reversal | [#7951](https://github.com/colophon-group/jobseek/issues/7951), [#8648](https://github.com/colophon-group/jobseek/issues/8648) |
| 4 | Introduce a native ordinary Go worker for one already ported HTTP/API family, then expand by effective profile | Go owns claim, fetch, enrichment, persistence and reschedule; exclusive cohort selection; retry/streaming/TDM behavior and DB effects match | [#7951](https://github.com/colophon-group/jobseek/issues/7951), [#7954](https://github.com/colophon-group/jobseek/issues/7954), [#7955](https://github.com/colophon-group/jobseek/issues/7955) |
| 5 | Close enabled monitor/detail coverage and remaining browser classes | Every enabled board maps to a proven Go HTTP/API or Go + Lightpanda route; ordinary execution shows correct output/freshness without Chromium for the completed profiles | [#7956](https://github.com/colophon-group/jobseek/issues/7956), [#7957](https://github.com/colophon-group/jobseek/issues/7957), [#7962](https://github.com/colophon-group/jobseek/issues/7962), [#7963](https://github.com/colophon-group/jobseek/issues/7963), [#9980](https://github.com/colophon-group/jobseek/issues/9980) |
| 6 | Remove remaining scheduled Python runtime consumers and package the crawler service without Python | Deploy/startup/schema migration, repair, currency refresh, healthchecks, activation/reversal and recurring jobs have a proven replacement or explicit tooling boundary | [#7964](https://github.com/colophon-group/jobseek/issues/7964), [#7958](https://github.com/colophon-group/jobseek/issues/7958) where enabled document work requires it |
| 7 | Complete fleet resource measurement, quiesced cutover/reversal and retirement | Current enabled-fleet reconciliation, whole-lane parity/efficiency, zero production Python workers/Playwright/Chromium owners, then legacy removal after the rollback window | [#7935](https://github.com/colophon-group/jobseek/issues/7935), [#8648](https://github.com/colophon-group/jobseek/issues/8648); criteria in closed [#7966](https://github.com/colophon-group/jobseek/issues/7966) |

For milestones 3 and 4, move the existing state machine rather than inventing a
new queue/control plane. Preserve local Postgres truth, durable source identity,
write fences, CDC/writer-floor publication, R2 pending/hash semantics, deletion
and gone policy, retry/backoff and scheduler conservation. Include meaningful
operational fault tests: crash after commit before acknowledgement, stale
terminal after ownership retirement, duplicate delivery, cancellation, lease
recovery, and partial deploy/cold reversal. Honor existing connection budgets.

For milestone 5, prefer a proven upstream HTTP/API route where it provides
complete data. Admit Lightpanda capability classes separately: DOM navigation
first, then response capture and interaction/frame/session/proxy cases only
where current enabled profiles need them. Keep Chromium as an explicit
transitional owner until its replacement is proven. Kandou remains dark until
its required-field failure is resolved on the actual extraction input.
Do not disable an enabled board or silently substitute an empty result to
claim browser retirement.

## First resumption session

1. Read this plan and the salary handoff at the saved head. Fetch current main
   and PR #10177; inspect state, draft, head/base OIDs, changed files and checks.
   Preserve the existing candidate and other contributors' changes. Reuse its
   branch in an isolated checkout or create an isolated checkout of that exact
   branch. Do not recreate salary extraction or edit the primary checkout.
2. Account for main drift and VERSION allocation. Rebase/update only where
   needed, then run affected checks and installed-image parity on the new head.
   The original green checks do not transfer to a changed candidate.
3. Record live committed release/image identities, active cohort receipt,
   owner/epoch, queue membership, selectors, mutation lock and service health.
   Recheck deployment holds. This plan authorizes no production promotion by
   itself; use the continuing session's operator authorization. The deliberate
   draft state must not be overridden merely to make a status green.
4. During an authorized deployment window, follow the supported cold procedure:
   rollback the current active cohort (recorded as cdom), clear the exact
   selectors at the full current promoted revision under the mutation lock,
   refresh merge authority and merge bound to the tested head, wait for terminal
   deployment success including promotion, stage selectors at the new full
   promoted revision, then activate through the supported wrapper.
5. Observe normal salary executions and canonical DB readbacks on the same
   stored bytes and supplied currency rates. Capture failures, scheduling,
   content hashes and scalar/R2 convergence. Save a deployed checkpoint or a
   precise blocked candidate checkpoint, then choose the next location slice.

Use [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md) and the current
[resumption operational record](24-go-lightpanda-resumption-plan.md). The
recorded selector helper is `scripts/migration-jsonld-selectors.py`, SHA-256
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`;
the recorded Kandou URL is `https://kandou.bamboohr.com/careers/310`.
Validate the helper and live revision before use; copy no historical activation
command without that refresh. Let bounded reconciliation finish if it holds
the lock. Do not bypass locks, edit live env files, force due/priority, restart
only part of the writer set or create duplicate publisher traffic.

## Pace and checkpoint discipline

Proposed pace: one bounded implementation slice per working session and one
production promotion at a time. Aim to promote a prepared slice within the next
one or two active sessions when required checks and operational evidence allow.
This is a planning target, not a deadline that overrides correctness. No
scheduled automation is created by this plan.

Maintain at most one candidate awaiting production promotion and one independent
offline implementation slice. A session should end with a reviewable PR and
verified evidence, a deployed milestone, or a concrete blocker with a named
next action. A checkpoint is a handoff, not proof that the full migration is done.

Run focused behavioral checks and the required CI/contracts once on the final
candidate. Repeat only after a change, failure or specific unresolved question.
Do not add another optional verification cycle after the evidence suffices.
While waiting for natural due work or a bounded maintenance job, proceed with
independent offline work; do not poll unchanged queues or generate extra origin
requests to produce evidence.

Track coverage by effective execution profile and production process owner,
with board IDs/selectors, route, capability class, parity evidence, deployed
revision, rollback path and remaining gap. Rank the next family using current
enabled/due volume, operational pain and ability to reuse Go code. Implementation
of unused families is not required for crawler service retirement.

After each promotion, update one concise current section in this plan, preserve
history in the resumption log, and link sanitized evidence. Reconcile the epic's
dated status when publishing the checkpoint under explicit issue-write
authorization. Keep the existing issue numbers instead of opening a duplicate
migration program. A portable checkpoint must contain:

- Full candidate and promoted revisions, immutable image/renderer identities,
  version, PR state and exact-head check results.
- Live owner/epoch and selector snapshot date, enabled-profile coverage delta,
  natural execution/output/failure evidence, and tested cold reversal.
- Committed sanitized evidence and reproducible input hashes; protected raw
  content stays outside Git. Local paths are optional aids, never the only
  restart record.
- One next slice, concrete blockers, and what evidence clears each blocker.

## Resource evidence and completion gates

Measure resources as each native boundary replaces a bridge, then repeat on the
complete migrated lane. The recorded 20x512 language-stage replay used
1.882/1.912 seconds Python CPU and 3.255/3.288 seconds Go CPU including bridge
and child. That stage was slower on those inputs. Parser parity and synthetic
density do not establish whole-lane production savings. Consolidating native
worker/enrichment execution is a proposed way to remove IPC overhead; measure
whether it helps.

Use single captured upstream inputs for offline comparisons and normal live
work for correctness/freshness. Compare the same workload, successful output,
concurrency, due window and publisher policy, including all Python bridges,
Go processes, browser/renderer, idle time and attributable support work. Report
CPU, sampled/peak/retained memory with measurement limits, correct completions
per resource unit, freshness, retries/errors/OOMs and attributable operating
cost. Missing observations and unequal input windows remain explicit gaps.
Use existing [runtime cost contracts](../apps/crawler/runtime-cost/) where
applicable; do not force the superseded projection into an actual-load decision.

Final acceptance requires current enabled-fleet coverage, canonical output and
intended DB effects, publisher-policy parity, conservation through cutover and
cold reversal, and better whole-lane efficiency without material throughput or
freshness regression. Agree and record the rollback observation window against
the actual schedules before deleting legacy assets; there is no arbitrary
seven-cycle or fixed-URL quota. Remove old code/images/credentials only after
the replacement is authoritative and that window has passed.

## Reasonable Python boundary

The target is zero Python execution required by the deployed crawler service:
workers, persistence, enrichment, browser execution, scheduled runtime
maintenance, startup/migrations, healthchecks and supported cutover/reversal.
Audit deploy scripts and timers as well as container commands. Merely changing
the worker executable or deleting a dependency from one image is insufficient.

Python can remain in separately packaged developer tooling, frozen test oracles,
one-off historical migrations and offline dataset/labeller/workspace workflows
where a rewrite adds little value. An operational exception must name its
consumer, whether it touches live state, why it remains, and its isolation and
removal criteria. A live mandatory Python step means service retirement is
incomplete even if ordinary crawling is already native Go.

Keep comparison code and rollback artifacts outside the lean production image
once reversal can use a complete prior digest-pinned release. Remove Playwright,
Chromium and legacy runtime-only dependencies only after every enabled browser
profile has a proven replacement. If a capability remains unresolved, report
the remaining owner and profile explicitly and continue useful migration work.
