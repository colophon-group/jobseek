# Go + Lightpanda migration: resumption plan

Status: implementation checkpoint, 2026-09-24. The initial review used
`origin/main` `6abfa52e1b061f2898a46355c48105accd1c01f9`; the B0 producer
implementation merged as `dcdc407ba836d75ec93e7416faa5f9fdabd41001`.
The fixture admission gate has passed; [#8648](https://github.com/colophon-group/jobseek/issues/8648)
tracks current production c1 admission evidence.

## Implementation: SmartRecruiters monitor and detail, v0.13.874

The Go SmartRecruiters binary now implements ordinary publication discovery,
all three configured localized identity modes, and scheduled provider detail
extraction. Default-off monitor/detail selectors are independent. The unchanged
writer retains queue, database, confirmed-drop and enrichment policies. See
[the module contract](../apps/crawler/go/smartrecruiters-monitor/README.md).

Offline verification covers 60 Python monitor cases and 26 detail cases,
including complete rich fields, source identity, language variants, pagination
and retry boundaries. CI also compares those outputs through the installed
binary with network disabled. The runtime reads bounded output and terminates
and reaps the child on cancellation or overflow. Scheduled details retain a
single request and empty content on non-200; Go additionally applies the
monitor's 1 MiB bound and TDM header/meta protections.

This is implementation evidence, **not a deployed production claim**. After
the release gates pass, cold-rollback c1 and clear the current 24 selectors
using the v0.13.873 procedure below. Deploy first, then stage a new exact
selector set against the promoted full revision. Admit ordinary monitor and
detail work on natural schedules, compare database/content/failure evidence,
and retain localized profile and whole-lane resource proof as explicit work.
No production due times should be changed for admission.

## Production checkpoint: 2026-09-27 — Go configuration sync v0.13.873

PR #10096 merged as `1f37e47ef036c1b08a5ca45dfca94cd9d7e3dbf6`.
[Deployment 36332710204](https://github.com/colophon-group/jobseek/actions/runs/36332710204)
succeeded. **Go now owns production configuration sync end to end:** CSV
preparation, taxonomy/company/description and board database transactions,
pending taxonomy resolution, Redis publication, dead-letter inspection and
Typesense publication. The Python CLI execs Go before opening runtime pools;
retained Python implementations are comparison/rollback references.

The actual release committed **7,879 boards** at 16:28:41 UTC, published
**7,879 schedules** and removed **133 retired-board queue entries** at
16:28:43 UTC. Dead-letter inspection found 24 records and zero unresolved.
Typesense taxonomy publication included 37,526 locations, 562 occupations,
36 seniorities and 186 technologies; the complete 6,013-company/7,879-board
sync finished at 16:29:23 UTC. Local mode-0600 deployment evidence is
`/tmp/jobseek-go-registry-deploy-36332710204.log`.

Read-only before/after identity digests match for all **6,039 database
companies and 8,013 database boards**, including retained historical rows.
All **7,879 enabled board configurations** in Redis match PostgreSQL URL, crawler
type, company ID, throttle domain and monitor/detail browser flags, with
zero mismatches. The sanitized record, including exact hashes and selector
values, is [the release evidence](evidence/go-registry-production-2026-09-27.json).

Required CI, installed-image parity and the exact-head deployment gate passed.
The first image check exposed a changed missing-mount diagnostic; the fix
preserves that contract and adds an installed Go dry-run with staged read-only
CSV data. Before promotion, supported c1 rollback retired epoch 78 at 79,
restored all five schedules, and left zero drops/write fences. Scheduled Go
reconciliation first completed 16 partitions and 356,179 local/remote rows
with zero differences; its live process was allowed to finish. All old 23
selectors were then cleared under the host mutation lock.

The new 24-selector set was staged against the exact promoted revision,
adding the existing strict `WORKABLE_GO_PERCENT=25` route after all four
explicit Workable boards completed natural Go runs with exact database URL
parity and zero failures. Its current union covers 15 boards, adding 11.
C1 is active at **epoch 80**, with accepted/audit_ok conservation: five ready
records, zero inflight or dead, and all services healthy. An exporter snapshot
reported 194 successful documents, zero errors and zero lag. The current
monitor route census is **2,950 Go / 4,929 Python** out of 7,879 enabled
boards, with zero route-resolution errors. Broader natural Workable runs are
pending; no due times were forced.
Use `/tmp/jobseek-post-go-registry-selectors.py` for subsequent cleanup;
the older 23-selector helper no longer matches the live environment. Before
any later crawler deployment or selector mutation, supported `rollback c1`
must finish first. Then use the new helper in `clear` mode under
`/run/lock/jobseek-crawler-mutation.lock`, against the current full deployed
revision above and `https://kandou.bamboohr.com/careers/310`. Stage only
after successful promotion at the new revision, then reactivate c1.

The full #7966 gate remains open: Python workers and remaining extraction,
enrichment, persistence and browser profiles still own production work;
whole-lane resource/cost comparison and final retirement are unfinished.

## Production checkpoint: 2026-09-27 — Go queue sync v0.13.871

PR #10093 merged as `ce5dfd821ca6e6b95d0b79a9eb534c26ddd486ec`.
[Crawler deployment 36328634446](https://github.com/colophon-group/jobseek/actions/runs/36328634446)
and [host launcher deployment 36328634440](https://github.com/colophon-group/jobseek/actions/runs/36328634440)
both succeeded. Production configuration sync now publishes committed board
queues in Go. Its actual deployment log records **7,879 schedules enqueued
and 133 retired-board queue entries removed**, followed by dead-letter
inspection with zero unresolved entries. Go taxonomy/company publication
completed at 15:23:05 UTC. The mode-0600 evidence log is
`/tmp/jobseek-go-board-queues-deploy-36328634446.log`.

Required CI and installed-image parity passed before merge; Crawler Deploy
Gate was green on the exact ready head. Supported c1 rollback retired epoch
76 at 77, restored five schedules, and left zero terminal drops or write
fences. A scheduled Go reconciliation held the host mutation lock afterward;
it finished with service exit zero before all 23 selectors were cleared.
Image promotion occurred only after that cleanup. The host wrapper is now
attested at the merged revision and invokes Go directly. Source draft #10092
is superseded by this deployment.

All four workers started Go reapers on the new image. An initial exporter
snapshot reported 129 successful document exports, zero errors and zero lag.
The 23 selectors were staged against the exact new full revision above.
Supported c1 activation reached **epoch 78**, with all services healthy. Its
conservation audit returned accepted/audit_ok: five ready records, zero
inflight or dead. A read-only comparison of all **7,879 enabled boards**
found zero differences in Redis versus PostgreSQL board URL, crawler type,
company ID, domain and monitor/detail browser flags. The helper is
`/tmp/jobseek-go-queue-db-proof.py`; it performs no mutations or origin calls.
A later exporter snapshot reported 230 successes, zero errors and zero lag.
Natural Go Workable monitor cycles remain pending. The active selector helper remains
`/tmp/jobseek-post-go-maintenance-selectors.py`, with Kandou URL
`https://kandou.bamboohr.com/careers/310`. Before the next deployment, roll c1
back, then clear all 23 selectors under the mutation lock against the actual
current revision. Never reuse the older 22-selector helper.

At the v0.13.871 checkpoint, CSV preparation and the local PostgreSQL
transaction were still Python. The v0.13.873 release above replaces them;
workers, remaining extraction profiles and final whole-lane evidence remain
open under #7966.

## Production checkpoint: 2026-09-27 — Go maintenance v0.13.870

PR #10089 merged as `ec892dc2899228e3a72526eb7d2f4cd5820445f3` and
[deploy 36326098454](https://github.com/colophon-group/jobseek/actions/runs/36326098454)
succeeded. Go schema setup and taxonomy/company sync completed in the actual
deploy: 37,526 location, 562 occupation, 36 seniority, 186 technology, and
6,039 company documents; zero company deletions. Go dead-letter inspection
reported 23 entries (20 actionable, three superseded, zero unresolved).

[Go taxonomy proof 36326995545](https://github.com/colophon-group/jobseek/actions/runs/36326995545)
passed with every static document and all active collection schemas matching.
The completed full backfill proof and resource limits are recorded in
[the production evidence](26-go-typesense-backfill-production-evidence.md).

Supported c1 rollback retired epoch 74 at epoch 75, restored all five
schedules, and left zero drops or write fences. The old 22 selectors were
cleared before merge. After deployment, 23 selectors were staged against the
new full revision, adding the four captured Workable boards documented in
[their evidence record](25-go-workable-production-evidence.md). Supported c1
activation reached epoch 76; all services are healthy. The conservation audit
returned accepted/audit_ok: five ready records, zero inflight or dead. Workers
run the Go lease-reaper child. A point-in-time exporter read reported 5,151
exported documents, zero document errors, and zero lag.

The first bounded Go reconciliation slice completed at 14:50:48 UTC through
the existing attested host launcher, which execs Go via the compatibility
CLI. Run `6f0ca3ad-8671-4291-b998-7f33f33eb8b9` checked 355,623 local and
remote rows across 16 partitions, repaired one difference, and left zero
unresolved. The launcher exited zero; its mode-0600 local log is
`/tmp/jobseek-go-reconciliation-ec892dc2.log`. All four workers reported Go
reapers without errors. The six original maintenance drafts were closed as
superseded by deployed #10089. Natural Go Workable cycles remain pending;
do not force them.

For that v0.13.870 release, mutation required supported `rollback c1`, then
clearing **23** selectors under the host lock with
`/tmp/jobseek-post-go-maintenance-selectors.py clear`, the actual deployed
full revision above, and `https://kandou.bamboohr.com/careers/310`. The older
22-selector helper no longer matches the live set. PR #10093 subsequently delivered the Go board-queue publisher, direct host
launcher and reaper pool-limit/budget correction; see the newer checkpoint
above. The full #7966 migration remains open.

## Implementation checkpoint: Go board sync queue publication

The next slice routes configuration sync's committed board schedules and
retired-board cleanup to `go-typesense-exporter --sync-board-queues`. Go owns
the existing Lua calls, board hash writes, provider delay keys, and bounded
1,000-board pipelines. No Redis publication occurs before the local database
transaction commits. Redis errors abort sync; ambiguous transport writes are
not automatically replayed. The child is bounded and reaped on cancellation.

The Redis fixture compares every stored key against the Python publisher,
including repeated execution, both worker types, first-time/recurring work,
existing leases, repair deadlines, rate/rotation floors, and corrupt state.
A 1,001-board case covers the batch boundary and stops later batches/removals
on failure. This implementation is deployed in v0.13.871. CSV/local PostgreSQL sync,
workers and remaining extraction/profile stages still require Go migration.

## Implementation record: Go configuration sync v0.13.873

The next configuration-sync port is on `fix-crawler/go-registry-sync`, created
from then-latest main `334f5631d` in an isolated worktree and subsequently
integrated with #10093 and latest main `012191b81` (#10095). The candidate
was subsequently deployed by #10096 as recorded above. Both `crawler sync` and the standalone
`python -m src.sync` entrypoint exec `go-typesense-exporter --sync-registry`.

Go now reads CSVs while preserving Python/Polars null versus quoted-empty
cells, BOMs and multiline CRLF; prepares canonical taxonomy, company and
description SQL arguments; computes board metadata and monitor fingerprints;
and reproduces browser/fallback routing and provider throttle hosts. The
retained Python functions generate the comparison evidence without publisher
traffic. Canonical SQL and registered route facts are embedded contracts.

Actual repository comparisons passed with the race detector:

- 6,013 companies and 5,876 descriptions, plus taxonomy tables: all CSV cells
  and all 14 prepared SQL calls match.
- 7,879 boards: metadata, fingerprints, monitor/detail browser decisions and
  throttle keys match. An additional 493 synthetic cases exercise registered
  types, fallback chains, numeric/Unicode settings, malformed configuration,
  and provider metadata/URL identity boundaries.

Local mode-0600 oracle artifacts are
`/tmp/jobseek-go-registry-repository-fixture.json` and
`/tmp/jobseek-go-registry-board-fixture.json`. Run the Go preparation tests with
`REGISTRY_TEST_FIXTURE` and `REGISTRY_BOARD_TEST_FIXTURE`, respectively.
The complete local transaction, board identity/rehome/recovery effects,
pending taxonomy resolution, installed read-only data-mount checks and
post-commit Redis/Typesense orchestration are now implemented in Go. Real
PostgreSQL fixtures verify stable IDs, recovery deadlines, runtime metadata,
posting rehomes, removal/reappearance, transaction rollback and ambiguous
commit acknowledgements. A real Redis fixture verifies no queue effects on
local failure. Index tests repair the covering index and prove lock release.
All 6,013 companies and 7,879 boards also committed successfully from the
actual repository into an isolated local PostgreSQL fixture, with no external
publication. Go matches 5,915 Python occupation-resolution samples. The
technology-miss query uses explicit integer casts to avoid ambiguous array
inference; existing populated fields and technology arrays remain intact.

The module race suite, vet and focused CLI tests passed. Required CI and the
installed-image checks passed before the supported rollout above. Python
remains the retained oracle; Go owns deployed local sync. The latest main #10095 crawler deployment 36330596574 was correctly
held by the active B0 receipt; production remains the healthy v0.13.871
checkpoint above. This candidate includes #10095 and must use the normal
cold rollback and exact 23-selector sequence. Do not bypass that guard.

## Historical release candidate: Go maintenance v0.13.870

The completed implementations from #10072, #10074, #10076, #10077, #10085 and
#10088 are assembled on `fix-crawler/go-maintenance-transition`, created from
latest main `52e8c7b6a`. The individual sections below record implementation
history; the unified candidate supersedes their separate release order.
This candidate was delivered by #10089 as recorded in the production checkpoint
above; the six source PRs have been retired. The following describes its
original release order, not another outstanding validation gate.

One crawler image now includes Go reconciliation, exact taxonomy verification,
schema setup, taxonomy/company publication and posting rename updates,
dead-letter inspection/recovery, and the supervised expired-lease loop. Shared
CLI conflicts retain every command. The protected maintenance chain invokes
Go backfill, Go reconciliation, then Go taxonomy verification under its existing
host lock; schema setup remains part of deployment and sync remains post-commit.
This keeps the existing completed backfill implementation and cold rollback
image, queue guards, cursor fences, credential scopes and publisher policies.

Release only after the already-running production proof 36317322087 finishes.
Then re-read live revision/receipt and exact PR checks, use supported c1
rollback, clear all 22 selectors under the mutation lock, promote the combined
candidate, stage at the new full revision and reactivate c1. Record normal
worker/reaper health, zero queue loss, Go schema/sync output, taxonomy parity,
CDC catch-up and the next bounded scheduled Go reconciliation result. The
systemd wrapper's separately attested direct-Go launcher follows **after** the
new binary is deployed; its current compatibility CLI already execs Go in the
candidate image.

CSV/local transaction sync, Redis board setup, worker claims/heartbeats and
processing, remaining monitor/detail/browser profiles, final whole-lane
resource/cost proof and retirement remain open under #7966. Do not equate this
maintenance release with completion of the full migration.

## Implementation slice: 2026-09-27 — Go reconciliation

The next release adds `go-typesense-exporter --reconcile`, replaces the
Python CLI runtime through `exec`, and invokes Go directly in the full
backfill maintenance chain. It retains the existing reconciliation ledger,
256-partition cursor, shared exporter fence, exact payload comparisons,
bounded complete-stream retries, repair/readback/source-stability checks,
legacy bucket cleanup, and durable candidate-order readiness receipts.
Bootstrap cleanup also holds the shared exporter fence through its local
absence checks and verification. No CDC cursor or exporter owner is changed.

The integration tests execute the actual state migrations against isolated
PostgreSQL schemas and exercise real HTTP imports/exports and cursor locks.
They prove that an ambiguous acknowledgement leaves the partition unadvanced,
a resumed repair converges, the full 256-partition cycle persists its evidence,
orphans are removed, cancellation records interruption, and competing owners
or stale in-memory receipts cannot establish a successful proof. Local
PostgreSQL 18, Go race/vet checks, the CLI/deployment tests, and workflow tests
passed. Production reconciliation remains on the previous runtime until this
release is deployed and its normal bounded run is observed.

The systemd wrapper has a separate installed SHA contract. Keep its current
`crawler reconcile` launcher for this image rollout; the new CLI immediately
execs Go. After this image is live, change the wrapper to invoke Go directly
through the supported reconciliation-host deployment. Installing that direct
wrapper before the binary exists would break the previous image. Taxonomy
verification, configuration sync, and other Python-owned stages remain on the
full migration backlog. The full #7966 gate is still open.

## Implementation checkpoint: 2026-09-27 — Go Typesense sync

`fix-crawler/go-typesense-taxonomy-sync` builds on taxonomy verification #10074
and implements the post-commit Typesense publication stage in Go. It is
implemented and locally verified, **not deployed**; release after the pending
reconciliation, verification, and schema-setup slices. The live protected
backfill proof still owns the host mutation lock.

The Go stage publishes complete location, occupation, seniority, technology,
and company documents from one static database snapshot. Company details
include all localized descriptions and industry names. Imports are bounded
to 1,000 documents. Exact company censuses preserve the acknowledgement,
missing-ID, duplicate/pagination, 50-document/1% deletion-budget, and final
convergence gates. Normal active/year counts come from the same Typesense
facets as the web and final refresh, avoiding initial seniority/technology
posting scans; local taxonomy counts remain bootstrap fallbacks. The final
Go refresh and typeahead invalidation are retained. Parent cancellation reaps
the Go child, and nonzero exit prevents a successful sync result.

Complete document parity matches the retained Python producer. A real
PostgreSQL + HTTP fixture executes the Go runtime through five full imports,
five count updates, twelve facet reads, and both company censuses. It has no
posting table, so passing proves that normal count reads stay in Typesense.
Prune-budget/failure, exact-pagination, import-bound, and child-lifecycle tests
also pass locally. CI and real production output/resource evidence remain
pending. Python still owns CSV/local transaction writes, Redis board effects, and
deadletter reporting; #7966 remains open.

The same sync slice now also owns pre-transaction name snapshots and posting
rename updates in Go. The before-map travels over a bounded stdin JSON handoff;
Go re-reads names under the exporter fence and processes affected postings in
1,000-row UUID keyset batches. Technology-name order/duplicates and existing
per-document rejection behavior are preserved. It never changes CDC cursor or
owner. The real PostgreSQL test covers 1,001 affected postings, an unaffected
row, null technology IDs, and fence release after ambiguous acknowledgement.
The Python snapshot/rename implementations remain offline references only.

## Implementation checkpoint: 2026-09-27 — Go taxonomy verification

The next release slice on `fix-crawler/go-typesense-taxonomy-verification`
ports `verify-typesense-taxonomies` to Go. It is implemented and locally
verified; **it has not been deployed or proven against production yet**.
Release it after reconciliation PR #10072 and after the active protected
backfill proof releases the host mutation lock.

The new runtime reads all five static taxonomy/company contracts in one
read-only repeatable-read PostgreSQL snapshot, checks six live schemas, and
compares every remote document with pages bounded to 250. It preserves exact
Python evidence hashes, missing versus null fields, localized aliases,
hierarchy membership, and redacted mismatch details. It fails on incomplete
or repeating pagination and emits one JSON record. Both the compatibility
CLI and protected maintenance route to Go; no Python verifier fallback is
used. Retained Python code is an offline oracle/cold-rollback reference.

Tests compare complete Go/Python evidence for ready and drifted collections;
exercise malformed/count-changing pagination, ambiguous numeric values,
hierarchy failures, schema differences, cancellation and error redaction;
and execute all nine SQL queries against PostgreSQL while another connection
commits a taxonomy update between reads. All Go race tests, nine focused
Python tests, 90 workflow tests, and seven web safety assertions pass locally.
CI, production verification, and resource evidence remain pending.
Configuration/taxonomy sync and schema setup are still Python production
work, so this slice does not satisfy the complete #7966 gate.

## Implementation checkpoint: 2026-09-27 — Go Typesense schema setup

`fix-crawler/go-typesense-schema-setup` implements the next deploy-time Python
owner in Go, intended after reconciliation #10072 and taxonomy verification
#10074. **This branch has not been deployed.** The production backfill proof
still holds the mutation lock; do not interrupt it to release this slice.

Deployment invokes `go-typesense-exporter --setup-schemas` directly using the
same Typesense-only credential scope and maintenance provenance. CLI/operator
wrappers exec Go. All seven collection definitions, aliases, search token
configuration, one-field index rebuilds, missing-field additions, explicit
force behavior, and memory-delta evidence are preserved. Ambiguous synchronous
PATCH timeouts are observed and re-read before retry; schema requests retain
the one-hour timeout and two-hour per-collection repair deadline.

Local Go race/vet tests pass, including existing-alias preservation,
concurrent collection creation, timeout-after-apply without replay,
busy-operation observation, missing status endpoint fallback, and bounded
cancellation. The embedded schema is compared with the retained Python source;
94 Python/schema/deployment tests and 90 workflow tests pass. CI and production
setup/readiness evidence are pending. CSV/configuration/taxonomy sync remains
Python and full #7966 completion remains open.

## Implemented next: Go expired-lease recovery (not deployed)

The scheduler's reaper loop now runs in a supervised Go process. It owns the
interval, both Redis lane sweeps and the dead-letter lifecycle join. The
remaining Python worker is a process/metrics adapter for this stage; it no
longer decides when or how expired leases recover. The canonical Lua and
publisher/queue policy are preserved. The frozen Python fixture checks exact
Redis state across 16 cases and three consecutive sweeps per case, including
batch bounds, unchanged due times, poison strikes, repair deadlines, missing
configs, ready tiers/rotation and the Lightpanda ownership guard. Real Redis
and PostgreSQL also exercise the complete sweep/dead-letter/classification
tick. Child cancellation, early exit, force-kill fallback and metric labels
are covered at the Python supervisor boundary.

This release remains queued behind the full maintenance proof and earlier Go
maintenance PRs. Do not deploy over the running mutation lock or claim this
completes worker scheduling, the full pipeline or #7966.

## Progress: 2026-09-27 13:40 UTC — backfill complete, proof running

The Go phase of [maintenance 36317322087](https://github.com/colophon-group/jobseek/actions/runs/36317322087)
finished at 13:27:38 UTC: **5,669,012 acknowledged documents in 5,472.035 seconds**.
The existing fresh full Python reconciliation then started in the same
container under the same mutation lock. At 13:36:35 UTC it had completed
partition `1f`, with zero unresolved differences in that partition. The full
256-partition proof and following taxonomy verification are still pending;
do not deploy over this process or declare final parity yet.

The final Go-phase cgroup sample at 13:27:37 UTC recorded 784.207 CPU-seconds,
79,636 KiB process RSS and 108,976 KiB process high-water RSS. Across 346
successful Go-phase samples the maximum sampled cgroup memory was 100,868,096
bytes. Sampling began after process startup and cgroup memory.peak is unavailable.
These numbers describe this maintenance container only; they do not prove
whole-lane efficiency or a comparison against the Python backfill.

Implementation PRs #10072 (reconciliation), #10074 (taxonomy verification),
#10076 (schema setup), and #10077 (taxonomy/company sync and posting rename
updates) are queued behind this proof. They are not deployed. The next
configuration-sync substage now implemented in Go is the read-only dead-letter
lifecycle join shared by sync, worker metrics and operator inspection. Its
Python-oracle fixtures and real PostgreSQL/read-only Redis integration cover
classification, batch boundaries, corrupt authority and membership preservation.
The same PR now implements explicit retry/prune in Go, including an atomic
Redis transition and a PostgreSQL row lock across the mutation boundary.
Tests prove due-time preservation, schedule deduplication, changed-config and
changed-authority refusal, superseded-inflight protection, and exact-member
replay refusal. These changes are not deployed. CSV/local transaction sync,
remaining scheduler/worker stages, and fleet-wide profile migration remain.

## Production checkpoint: 2026-09-27 — Go Typesense backfill

[PR #10069](https://github.com/colophon-group/jobseek/pull/10069) merged as
`fcb19acf39ab8f55687fa870de4e2e15dd21beb0`.
[Deployment 36316456325](https://github.com/colophon-group/jobseek/actions/runs/36316456325)
successfully promoted crawler v0.13.864. The operator backfill CLI now execs
`go-typesense-exporter --backfill`; the maintenance workflow invokes it
directly. The Go PostgreSQL integration fixture, race tests, installed-image
parity, and required CI passed before merge. A stale deployment-test command
expectation was corrected; the complete maintenance proof chain remains
under one host mutation lock and fails on any unsuccessful step.

Before deployment, supported c1 rollback retired epoch 72 at epoch 73,
restored all five schedules, and reported zero terminal drops or remaining
write fences. All 22 exact selectors were cleared under the mutation lock
against the old release snapshot. After promotion they were staged against
the new full revision above, and supported activation restored c1 at epoch
74. The queue conservation audit returned `accepted/audit_ok`: five ready
records, zero inflight, and zero dead. Workers, browser, drain, producer,
executor, claimant, and Redis are healthy; the live exporter still reports
the durable owner as `go`. No publisher due scores or duplicate origin
requests were forced.

The exact-revision full proof is running in
[maintenance 36317322087](https://github.com/colophon-group/jobseek/actions/runs/36317322087).
It runs Go backfill, then the existing full fresh Typesense reconciliation
and taxonomy verification. **Production backfill output and final index
parity remain pending until that run completes.** Read-only cgroup CPU and
memory samples are being captured to local mode-0600
`/tmp/jobseek-go-backfill-fcb19acf-resources.jsonl`; these describe the
maintenance container, not same-workload whole-lane efficiency or cost.

Before another crawler deployment or selector change, use the supported c1
rollback, then clear all 22 selectors under
`/run/lock/jobseek-crawler-mutation.lock` with
`/tmp/jobseek-post-go-sitemap-selectors.py clear`, full release revision
`fcb19acf39ab8f55687fa870de4e2e15dd21beb0`, and
`https://kandou.bamboohr.com/careers/310`. The running maintenance operation
also holds that lock; let it finish before another deployment. Never edit
the host environment manually.

The next implementation slice ports resumable reconciliation. Comparison and
bounded Typesense streaming helpers are checkpointed on
`fix-crawler/go-typesense-reconciliation` at `e94ed35e6`; they are not wired
into production. PostgreSQL repair, durable partition/run progress, CLI and
timer routing, and integrated failure/restart tests remain to implement.
Python reconciliation and taxonomy verification still run in production,
and the complete [#7966](https://github.com/colophon-group/jobseek/issues/7966)
gate remains open.

## Implementation slice: 2026-09-27 (before deployment)

Read-only inspection confirms that production already runs the Go Typesense
posting exporter, despite the older exporter section below. The host remains
at `f4520232f1f8c0905a68586dbfe4b736b7f17e79` with c1 epoch 72 active and
healthy. Scheduled count refreshes also invoke Go. The next runtime slice
ports full posting backfill to `go-typesense-exporter --backfill` and routes
both the operator CLI and maintenance workflow through it. It preserves the
shared cursor fence, commit-safe cutoff, full posting/company projection,
per-document acknowledgements, bounded retries, and cursor monotonicity.
The follow-on full reconciliation and taxonomy verification remain Python.

The backfill slice is not yet deployed or proven on production output. Its
real PostgreSQL fixture exercises fence exclusion, ambiguous acknowledgements,
restart/replay, and final cursor persistence. Before deploying, use the
supported c1 rollback and exact 22-selector cleanup described in the production
checkpoint below; restage and reactivate only against the promoted revision.
See the [Go Typesense runtime commands](../apps/crawler/go/typesense-exporter/README.md).

## Production checkpoint: 2026-09-25 20:22 UTC

This section supersedes the earlier pending Verity observation below.

[PR #10031](https://github.com/colophon-group/jobseek/pull/10031) fixed
Compose propagation of `SITEMAP_GO_BOARD_IDS` after the first v0.13.861
release left the selector absent inside workers. It merged as
`f4520232f1f8c0905a68586dbfe4b736b7f17e79`; [deploy run 36178705066](https://github.com/colophon-group/jobseek/actions/runs/36178705066)
successfully promoted v0.13.862. The ARM64 B0 fixture completed successfully;
this selector wiring does not change the Go sitemap parser.

Before deployment, the supported c1 rollback retired routing epoch 70 at 71,
restored all five schedules, and reported zero terminal drops and write
fences. The 22 exact selectors were cleared under the host mutation lock with
`/tmp/jobseek-post-go-sitemap-selectors.py`, the old full release revision,
and Kandou detail URL `https://kandou.bamboohr.com/careers/310`. After
promotion, the same 22 were staged against the exact new release snapshot and
c1 was reactivated at routing epoch 72 with five selected schedules. Worker1
now sees Verity's exact `SITEMAP_GO_BOARD_IDS` value
`c4779214-ef92-4261-98fb-ae64f264fd23`. Workers, browser, drain, producer,
executor, claimant, and Redis are all healthy.

The read-only epoch-72 queue conservation audit returned `accepted/audit_ok`:
five records, all ready, zero inflight or dead.

Verity's last Python monitor completed naturally at 19:12:41 UTC, before
activation. The first selected Go sitemap monitor completed on its normal
schedule at 20:21:21 UTC. It returned three URLs from one HTTP response
(911 bytes), with sorted URL SHA-256
`3427540e27b9a618103f26eb9a178000e5296b1b4db0f3e0e09cc9672acda288`.
The existing board writer persisted exactly three active PostgreSQL rows with
the same sorted digest; `last_success_at` advanced to 20:21:21 UTC, the next
check to 21:21:21 UTC, and `consecutive_failures` remained zero. No due score
or duplicate origin request was forced. The post-run epoch-72 queue audit
again returned `accepted/audit_ok`, five ready records, and zero inflight or
dead; all nine runtime services listed above remained healthy. This proves
one selected Go HTTP monitor and its database effects, not fleet-wide output
or whole-lane resource parity. Before another crawler deploy
or selector mutation, cold-rollback c1, then clear all 22 selectors under
`/run/lock/jobseek-crawler-mutation.lock` with the same script in `clear`
mode, exact release revision `f4520232f1f8c0905a68586dbfe4b736b7f17e79`,
and the Kandou URL above. Never manually edit `.env`.

The full [#7966](https://github.com/colophon-group/jobseek/issues/7966)
remains open. Complete and prove every enabled monitor, detail scraper, and
browser profile in Go HTTP/API or Go + Lightpanda; move scheduling, extraction,
enrichment, persistence, drain, export, maintenance, and sync from Python;
remove production Playwright and Chromium after profile parity; measure
same-actual-workload whole-lane CPU, peak and retained RAM, correct output
density, and attributable cost; and exercise final quiesced cutover and cold
reversal without queue loss, stale writes, or duplicate origin traffic.

## Production checkpoint: 2026-09-25 18:43 UTC

[PR #10029](https://github.com/colophon-group/jobseek/pull/10029) merged as
`bf1437d6c97bc3ffc50c7b36f13992640b0bd070`; [deploy run 36172829219](https://github.com/colophon-group/jobseek/actions/runs/36172829219)
promoted crawler v0.13.861. The bounded Go sitemap parser from the read-only
pilot is packaged in the crawler image and accepts only an explicit
same-origin HTTPS `urlset`. The adapter runs the same Python URL filter,
allowlist, and transform stages before the existing board writer. It is
default-off; an unsupported index or changed configuration fails closed. The
accepted [production shadow](https://github.com/colophon-group/jobseek/issues/8641)
previously matched Python's URL counts and hashes for Acosta/Dee Set and
Verity Breezy. Its exact board ID is
`c4779214-ef92-4261-98fb-ae64f264fd23`.

Before that deploy, the supported c1 cold rollback retired epoch 68 at epoch 69 and
restored all five schedules with zero terminal drops or write fences. The old
21 exact selectors were cleared under the host lock. After promotion, 22 exact
selectors were staged at `bf1437d6c97bc3ffc50c7b36f13992640b0bd070`
using `/tmp/jobseek-post-go-sitemap-selectors.py` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. Supported c1 activation selected
five schedules at epoch 70; workers, browser, drain, producer, executor,
claimant, and Redis are healthy. Post-activation inspection found that Compose
did not pass `SITEMAP_GO_BOARD_IDS` into workers, so Verity remains
Python-owned despite its host selector. The follow-up v0.13.862 release wires
that key. Before deploying it, cold-rollback c1 and clear all 22 exact
selectors with the same script, release revision, and Kandou URL under the
host mutation lock. After promotion, stage them again at the new exact
revision and reactivate c1. Then observe Verity's natural Go URL digest,
active PostgreSQL URLs, and board failure count; do not force its due time or
send a duplicate origin request. The full
[#7966](https://github.com/colophon-group/jobseek/issues/7966) completion
goal remains open while Python and Chromium own production work.

## Production checkpoint: 2026-09-25 15:16 UTC

[PR #10024](https://github.com/colophon-group/jobseek/pull/10024) merged as
`c09c1d519` and [deploy run 36148928512](https://github.com/colophon-group/jobseek/actions/runs/36148928512)
promoted crawler v0.13.858. The Go Workable detail scraper is installed but
defaults off. A naturally scheduled KI Insurance Python detail response was
captured without another origin request. Its 5,010 exact bytes, SHA-256
`0b7b1f982990b630b5b1f9ef51c28437b520761af8e849b5610ae6ac86b0f0fd`,
produced identical Python and Go values for all seven populated detail fields.
The Python scrape succeeded. KI Insurance has 18 active jobs, zero board
failures, and no browser requirement.

The supported c1 rollback retired epoch 65, restored all five schedules,
and confirmed zero dropped tasks and write fences. Twenty exact selectors
were cleared under the mutation lock; 21 were staged at exact release
`c09c1d5191f779280e088c6b6131a1c66426835f` with
`/tmp/jobseek-post-workable-go-detail-pilot-selectors.py` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. C1 reactivated at epoch 66 with
all five schedules; services are healthy. The added Workable Go detail selector
targets only KI Insurance board `aef95fd2-55ea-43cd-9aa6-9bd541c2bc4e`.
Its natural Go scrape and persisted content readback are pending. The list
capture selector still targets four Python Workable boards; natural scheduled
responses are next due around 15:40–15:57 UTC. No due scores or extra origin
requests were forced. Personio 5% selects Silverflow and newly eligible
Eraneos Germany (five stable 21-job runs); Eraneos Go output is pending.
Before any further deploy or selector mutation, cold-rollback c1 and clear
those exact 21 selectors with that script, revision, and Kandou URL.

The next strict Lever code slice resolves five of the six remaining Python
boards: two explicit dotted tokens, two explicit tokens on canonical company
career URLs, and one unused `company` metadata value matching its derived
token. The sixth, Volta Medical, retains a JSON-LD detail scraper and remains
Python-owned. Live output and database effects for the five new routes remain
to be observed after deployment; this change cannot establish the full
[#7966](https://github.com/colophon-group/jobseek/issues/7966) gate while
Python and Chromium still own production work.

## Production checkpoint: 2026-09-25 14:26 UTC

[PR #10023](https://github.com/colophon-group/jobseek/pull/10023) merged as
`6c993efe5` and [deploy run 36145740751](https://github.com/colophon-group/jobseek/actions/runs/36145740751)
promoted crawler v0.13.857. The route census changed from 165 to 189 Go
Lever boards out of 195 enabled: 20 canonical direct URL boards now derive
their missing token as Python does, and four EU boards now derive the missing
region as Python does. Six Lever boards retain Python routing because their
configuration does not satisfy the strict Go guard. All 24 newly eligible
boards had zero consecutive failures before their first natural selected Go
cycle; live output and database readback for this cohort are still pending.
`LEVER_GO_PERCENT=0` reverses the default.

Supported B0 c1 cold rollback retired epoch 61 and restored all five retained
schedules before deployment. The 18 old selectors were cleared under the host
mutation lock. After deployment, the same 18 were staged with
`/tmp/jobseek-post-workable-capture-selectors.py` at revision
`6c993efe5780e38ed5f90730dbe881f52c485501` and Kandou URL
`https://kandou.bamboohr.com/careers/310`; c1 reactivated with five
schedules at epoch 62. Workers, browser, drain, producer, executor, claimant,
and Redis are healthy. Before another crawler deployment, cold-rollback c1
and clear these exact selectors with the same script, revision, and URL.
Workable list capture on natural Python schedules remains pending; do not
force due scores or duplicate origin traffic. C2 Kandou stays dark.

[Draft PR #10024](https://github.com/colophon-group/jobseek/pull/10024)
adds default-off Go Workable detail extraction and passive capture for exact
scheduled detail jobs and Workable Markdown/public API list fallback bodies.
It is not deployed or selected. The full [#7966](https://github.com/colophon-group/jobseek/issues/7966)
completion gate remains unmet while Python and Chromium own production work.

## Production checkpoint: 2026-09-25 13:41 UTC

[PR #10022](https://github.com/colophon-group/jobseek/pull/10022) merged as
`6c8b7179b` and [deploy run 36140762256](https://github.com/colophon-group/jobseek/actions/runs/36140762256)
promoted crawler v0.13.856. It adds a default-off Go Workable list monitor
and passive capture of existing Python list responses. Before deployment,
supported c1 cold rollback retired epoch 59 and restored all five retained
schedules; the 17 exact old selectors were cleared under the host mutation
lock. After deployment, 18 exact selectors were staged with
`/tmp/jobseek-post-workable-capture-selectors.py` at revision
`6c8b7179b91265336d25cee32275ea8f1bebbb9a` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. The added selector captures
Workable responses for `pix4d,debiopharm,unit8,hack-the-box-ltd` on their
natural Python runs. No Workable board routes to Go yet. Supported c1
reactivation selected five schedules at epoch 60; workers, browser, drain,
producer, executor, claimant, and Redis are healthy. C2 Kandou stays dark.
Before any later crawler deployment, cold-rollback c1 and clear all 18
selectors with that exact script, revision, and Kandou URL.

Silverflow Personio completed its first natural selected Go monitor run at
13:24 UTC: four URLs, two responses, zero monitor or board failures. Its
sorted URL digest
`2a7048973d0c0e572788718e93234a4e1a10d8c8b21ad43a76f6aff49960a1ba`
matched all four active PostgreSQL rows after the existing writer ran. The
earlier exact-byte EN/DE replay matched all four full rich dictionaries.
This is a live monitor and persistence checkpoint, not a whole-lane resource
measurement. The first Mobiliar SuccessFactors Go RSS run is still queued;
do not force it or duplicate origin traffic.

[PR #10023](https://github.com/colophon-group/jobseek/pull/10023) is open
from the new main. It lets the Go Lever runtime derive token and EU region
from a canonical direct board URL just as Python already does. Twenty current
CSV boards have this strict tokenless shape. Merge it only after its gates
pass and after preserving the B0 cold rollback / selector procedure above.
The fleet remains largely Python-owned and does not satisfy
[#7966](https://github.com/colophon-group/jobseek/issues/7966).

## Production checkpoint: 2026-09-25 12:48 UTC

[PR #10021](https://github.com/colophon-group/jobseek/pull/10021) merged as
`e9a1421d4` and [deploy run 36134079773](https://github.com/colophon-group/jobseek/actions/runs/36134079773)
successfully promoted crawler v0.13.855. Strict direct Lever boards now use Go
by default; `LEVER_GO_PERCENT=0` remains the route reversal. Supported B0 c1
was cold-rolled back at epoch 57, restoring all five retained schedules with
no loss, then reactivated at epoch 58 after deployment. C2 Kandou stays dark.
All workers, browser, drain, producer, executor, claimant, and Redis were
healthy after the transition.

Seventeen temporary selectors were staged under the host mutation lock using
`/tmp/jobseek-post-lever-default-selectors.py` with exact revision `e9a1421d4`
and Kandou rendered-DOM capture URL `/careers/310`. The route census is 1,471
Go selections and 6,394 Python selections across 7,865 enabled boards. Before
another crawler deployment, cold-rollback c1 and clear those exact selectors
with the same script, full revision and Kandou URL; then stage the desired
post-release selectors and reactivate c1. The prior 20 natural Go
Greenhouse/Lever/Workday cycles had zero failures and exact active PostgreSQL
URL counts and digests. Their rich fields and whole-lane resource efficiency
were not established by that readback.

Silverflow Personio's natural Python monitor captured EN and DE XML at 12:18
UTC. Offline Go and Python replay of the exact same bytes matched all four
ordered rich-job dictionaries, including descriptions and German
localizations. The four active PostgreSQL URLs matched digest
`2a7048973d0c0e572788718e93234a4e1a10d8c8b21ad43a76f6aff49960a1ba`,
with zero board failures. Its exact Go selector is now active; the first
natural Go cycle is due after 13:18 UTC. The first Mobiliar SuccessFactors
Go RSS cycle remains queued. The Go Workable URL monitor stays default-off
pending live response capture and admission; its existing detail scraper
still runs in Python. None of these steps closes [#7966](https://github.com/colophon-group/jobseek/issues/7966).

## Production checkpoint: 2026-09-25 12:00 UTC

The latest crawler release is `4c620132c` (v0.13.854), deployed by
[run 36128889906](https://github.com/colophon-group/jobseek/actions/runs/36128889906).
Supported B0 c1 is active at epoch 56 with five retained schedules; c2
Kandou remains dark. Temporary selectors route 1,470 of 7,865 enabled boards
to Go monitors: 1,275 Greenhouse, 165 Lever, 21 Ashby, and nine individually
selected boards. Fourteen natural Greenhouse/Lever batch cycles since 11:43
completed with zero failures and exact active database URL count/digest
readback. This checks persistence of the selected URL inventory, not every
rich field or the whole crawler lane. The same-byte Mobiliar SuccessFactors
RSS replay matched all 71 rich jobs before its exact Go selector was staged;
its first natural Go cycle remains pending. A Silverflow Personio response is
being captured passively on its next normal schedule. Do not force either
board due or send a second request to its origin.

The next code release makes the strict 165-board Lever cohort Go by default,
while `LEVER_GO_PERCENT=0` remains the immediate route reversal. Before any
crawler deployment, use the supported B0 c1 cold rollback, clear temporary
host selectors under the mutation lock, and verify the host environment
matches the release snapshot. After deployment, stage desired selectors and
reactivate c1. Python still owns most monitors and the board writer; this
checkpoint does not satisfy [#7966](https://github.com/colophon-group/jobseek/issues/7966).

The delivery goal is a complete Go crawler using self-hosted Lightpanda for
browser work and Go HTTP/API execution where that removes a browser need.
Python, Playwright, and Chromium leave production after every enabled profile
has equivalent output and the Go runtime owns scheduling, persistence, drain,
export, maintenance, and configuration sync. Temporary Chromium assignments
remain explicit during migration; eliminating them is a completion task, not
an admission condition for the first B0 cohort.

## Production checkpoint: 2026-09-24 20:20 UTC

This checkpoint supersedes the deployment and owner statements below. The
latest code baseline for further work is `origin/main` `3e9e737c4`.
[PR #9991](https://github.com/colophon-group/jobseek/pull/9991) merged the
guarded Go Typesense CDC exporter as `2ade431d9` and
[deploy run 36052100515](https://github.com/colophon-group/jobseek/actions/runs/36052100515)
successfully promoted that revision. The first deploy attempt failed before
the image changed because a temporary Elastic capture variable in the host
environment differed from the committed release. Its rollback restored the
old image and workers; the successful retry ran with both temporary pilot
variables removed. Clear pilot variables **entirely** before each future
deploy and check host environment attestation, excluding only `COMPOSE_FILE`.

The fenced production `python -> go` Typesense owner transfer succeeded at
about 20:13 UTC. Python exited on its ownership check, and the same exporter
service restarted under the Go entrypoint. At 20:19 UTC, Go had acknowledged
566 live documents with zero rejected documents, export errors, or unknown CDC
writers. The Typesense downstream availability metric was healthy, the process
was running without an OOM, and its cgroup used about 57 MiB of its 256 MiB
limit. The owner query returned `go`. This is a live ownership and correctness
checkpoint, not an equal-workload resource comparison for the whole crawler.
Keep the Go owner unless a concrete failure requires the documented reverse
transfer; never rewind the cursor.

The supported c1 rollback before deployment restored five due schedules to
Python at retired epoch 27. After the successful deploy, the one-board
Elevance Workday Go selector was staged again and confirmed inside a recreated
HTTP worker. The supported B0 c1 activation selected five boards at routing
epoch **28** and wrote `/home/deploy/.lightpanda-b0-active-v1` for the
`2ade431d9` image. Producer, executor, claimant, workers, browser, and drain
were healthy; the host mutation lock was free. C1's first retained detail is
due 2026-09-25 00:26 UTC. C2 remains dark because the Kandou required-title
mismatch has not been resolved. The Elastic natural response capture has not
arrived; its temporary capture variable is currently absent. Before another
crawler deploy, cold-rollback c1 with the supported wrapper, remove the
`WORKDAY_GO_BOARD_ID` line under the host mutation lock, and verify release
environment attestation. Reactivate c1 only after that deploy succeeds.

The completion gate remains [#7966](https://github.com/colophon-group/jobseek/issues/7966):
most enabled boards still use the Python worker and Chromium routes. Next,
observe c1's retained natural detail, continue the exclusive Workday monitor
cohort with same-input resource evidence, resolve a concrete Lightpanda
capability or Go HTTP profile, and replace the remaining Python runtime stages
and board families. Do not infer whole-fleet efficiency from these pilots.

## Execution checkpoint: 2026-09-24

This checkpoint supersedes the older epoch and pending-run statements below.
On the prior deployed revision `1f8e9113bfc4730ca9ba9ada183dbdd507827fa4`,
two natural Elevance Go Workday cycles completed at 16:18 and 17:25 UTC.
They found 308 and 309 URLs in 16 requests each with zero transport errors.
Both cycles' sorted URL digests matched their exact database readbacks after
the existing Python board writer ran. The publisher changed between cycles;
these are live persistence results, not same-input Python-versus-Go resource
comparisons. [#7955](https://github.com/colophon-group/jobseek/issues/7955#issuecomment-5818925063)
records the second run.

[PR #9989](https://github.com/colophon-group/jobseek/pull/9989) merged as
`d0077bdc3459c5f2380caae8e55b33a387b811d3` and
[deployed successfully](https://github.com/colophon-group/jobseek/actions/runs/36036386828).
It combines the default-off rich Elastic Go Greenhouse monitor, one scheduled
Elastic response capture, Kandou rendered-DOM capture, Booking main-response
and DOM capture, and the c2 legacy scrape-enqueue owner guard. Earlier draft
PRs #9979, #9987, and #9944 were closed as superseded. Synthetic same-byte
Greenhouse Go/Python field replay passes; live Elastic capture and whole-lane
measurement still gate exclusive Go activation. Kandou's intermittent
required-title failure keeps c2 dark.

For this deploy, supported c1 cold rollback retired epoch 25 and restored all
five future due schedules to Python before the crawler image changed. The
temporary one-board Workday selector line was removed entirely under the host
mutation lock. After deployment, the one-board selector and Elastic capture
path were staged under that lock, and supported c1 reactivation transferred
the same five due scores to Go at routing epoch **26**. The active receipt,
producer owner, and route agree; no B0 job is inflight or dead, all services
are healthy, and the mutation lock is free. The first existing c1 detail is
due 2026-09-25 00:26 UTC, so live Lightpanda detail output and production
whole-lane resources remain unmeasured. Remove the Workday selector line
entirely before any later crawler deploy, and cold-rollback c1 first.
[#8648](https://github.com/colophon-group/jobseek/issues/8648#issuecomment-5819378524)
records the transition. The fleet remains Python-owned outside the named
pilots; zero Python and zero Chromium are not achieved.

At 18:30 UTC a third natural Elevance Go Workday cycle found 311 URLs in 16
requests with zero transport errors. Its sorted URL digest
`5eb7dd7d88ec5730e840c720acb475f094106b3aa72c0b2b772165c9a62aa099`
matched the exact 311 active PostgreSQL rows. Three new details were
scraped successfully by the existing Python detail owner. The publisher
changed again, so this remains output/persistence evidence for the Go list
monitor rather than a whole-lane resource comparison.

## Go Typesense exporter slice

An isolated branch based on deployed `d0077bdc` now contains a default-dark
Go Typesense CDC exporter. It uses the current Python export cursor encoding,
exact CDC cutoff SQL, shared PostgreSQL advisory fence, taxonomy inputs,
company JOIN, per-document Typesense acknowledgements, and bounded downstream
backoff. It checks a durable `export_owner:typesense:job_posting` row inside
the fence before every import. A missing row means Python; Python checks the
same row inside the same fence. Neither runtime can import under the other's
ownership. The owner-aware exporter entrypoint chooses the process at each
container start, including after an ordinary deployment rewrites `.env`.
The Go process retains the existing exporter staleness, Typesense health,
CDC cutoff, error, and bounded Redis queue-depth metrics on port 9093.

The production Python exporter is still authoritative. A read-only snapshot
of 200 recent production postings on 24 September projected identical
Python and Go Typesense documents across all fields. Go loaded its own live
taxonomy maps and read the exact same database cutoff as Python. One
occupation ancestor array initially differed only in order; both runtimes
now emit stable sorted order, and the 200-row rerun matched. The comparison
ran in a separate, memory-bounded container and sent no publisher or Typesense
requests. The production exporter restarted once after an initial comparison
attempt exceeded its 256 MiB container limit; it recovered and resumed
exporting. Do not run this comparison inside the live exporter again. The
Go writer has not imported a production document or advanced the cursor.

The handoff command is an explicit compare-and-swap:

```bash
cd /home/deploy
flock -x /run/lock/jobseek-crawler-mutation.lock \
  docker compose run --rm --no-deps \
  -e GO_TYPESENSE_EXPORTER_TRANSFER=1 exporter \
  /usr/local/bin/go-typesense-exporter --transfer-owner python go
```

Run it only after deploying the owner-aware image with Python still selected,
checking the cursor and index lag, and holding the host crawler mutation
lock. The command waits for the in-flight Python tick through the shared
fence, verifies the existing cursor, and records Go ownership. The Python
process exits on its next ownership check; Compose restarts the exporter
service, whose entrypoint selects Go. Confirm the process, port 9093 metrics,
cursor progress, acknowledgements, index lag, and absence of document drops.
If the Go process fails or those checks fail, reverse ownership with the same
command ending `--transfer-owner go python`; the Go process exits and the
entrypoint restarts Python from the retained cursor. Never rewind the cursor.
Before the first owner-aware image deploy, cold-rollback c1 and remove the
temporary Workday selector line from the host environment as described above;
reactivate c1 only after the new release passes its normal deployment gates.

## Current state

- [#7935](https://github.com/colophon-group/jobseek/issues/7935) reset the
  initial effort to bounded, independently useful pilots. The 10M-board
  projection and first-pilot gates in
  [the older migration design](23-go-lightpanda-migration.md) are historical.
  The owner subsequently made complete retirement of Python, Playwright, and
  Chromium the delivery goal in [#7966](https://github.com/colophon-group/jobseek/issues/7966).
  Its enabled-fleet and whole-lane completion criteria govern final cutover.
- The bounded Go sitemap worker and read-only production shadow landed through
  [#8644](https://github.com/colophon-group/jobseek/pull/8644); the fleet
  benchmark landed through [#8660](https://github.com/colophon-group/jobseek/pull/8660)
  and [#8694](https://github.com/colophon-group/jobseek/pull/8694). Python
  still owns sitemap schedules and writes. The earlier
  [#8461](https://github.com/colophon-group/jobseek/pull/8461) and
  [#8524](https://github.com/colophon-group/jobseek/pull/8524) drafts are
  closed without merge; they are evidence, not candidate branches.
- [#8881](https://github.com/colophon-group/jobseek/pull/8881) merged the
  bounded B0 Go supervisor, Lightpanda renderer route, Python database-only
  executor, and explicit c1/c4 cutover tooling. Ordinary deployment is dark.
  Production c1 uses the separate, receipt-guarded activation overlay.
- [#8738](https://github.com/colophon-group/jobseek/issues/8738) landed the
  bounded Go producer authority through [#9874](https://github.com/colophon-group/jobseek/pull/9874).
  The 48-file predecessor at `d6d583515` was reassessed against concrete
  crash and ownership failures and reconciled with current queue semantics.
  About half of its addition was test code. The exact merged revision passed
  required CI and the crawler deploy gate; [deploy run 35845382807](https://github.com/colophon-group/jobseek/actions/runs/35845382807)
  succeeded with only the dark claimant in the ordinary service list. There is
  no evidence of enabled B0 traffic in that deployment.
- [#8648](https://github.com/colophon-group/jobseek/issues/8648) owns the
  remaining whole-lane admission. [PR #9880](https://github.com/colophon-group/jobseek/pull/9880)
  merged the real-producer harness. Its exact-source [ARM64 fixture run](https://github.com/colophon-group/jobseek/actions/runs/35861227140)
  exercised the real Go producer in all 16 arms with exact output, request,
  persistence, and queue parity. Median correct-URL density improved 3.42×
  at c1 and 6.71× at c4; peak memory and completion latency fell. CPU use
  rose from 12.88 to 23.06 seconds at c1 and 17.53 to 23.88 seconds at c4
  across the common due and retained measurement window. About 20 CPU seconds
  per arm came from the transitional Python executor. Production c1 reached
  routing epoch 14 with five Go-owned schedules. Its lightweight executor
  health probe cut measured idle CPU from 10 to 1 seconds per 20-second
  window. A cold rollback returned all five schedules to the legacy queue
  before [#9934's crawler deploy](https://github.com/colophon-group/jobseek/actions/runs/35903707755);
  the supported cutover reactivated c1 at epoch 16 afterward. For the
  [Go drain deploy](https://github.com/colophon-group/jobseek/actions/runs/35912138580),
  c1 was cold-rolled back again at retired epoch 17, with all five exact due
  scores restored, then reactivated on revision `4a88deaf` at epoch 18.
  Production B0 output, requests, and whole-lane capacity still need
  measurement. The public sitemap run in #7935 was inconclusive because the live source changed
  between arms; it is not a Python-versus-Go verdict.
- [#7959](https://github.com/colophon-group/jobseek/issues/7959) admitted
  pinned Lightpanda 0.4.0 for narrow B0/B1 nonproduction use. Its broader
  compatibility results do not justify moving interactive, frame, identity,
  or API-sniffer profiles. Chromium remains an explicit compatibility owner
  where those capabilities are unproven.
- [PR #9932](https://github.com/colophon-group/jobseek/pull/9932) merged and
  deployed the exclusive Go R2 drain. A counterbalanced 2,000-description
  ARM64 fixture preserved every object and database pointer while reducing
  CPU 3.75–3.86× and retained memory 9.5–10.1×. A
  [200-second production sample](https://github.com/colophon-group/jobseek/issues/7945#issuecomment-5802172400)
  observed 247 successful uploads, 17.72 ms CPU per upload, 21.9 MiB final
  cgroup memory, and exact R2/DB/pointer readback on five samples. The prior natural
  Python sample used 22.14 ms CPU per upload and about 118 MiB memory; the
  input sizes differed, so only the fixture is an equal-input CPU comparison.
  [PR #9935](https://github.com/colophon-group/jobseek/pull/9935) prepares
  the three validated production B0 origins; it remains undeployed pending
  c1's first due work.

## Next slice: one real Go-owned B0 cohort

1. **Producer implementation landed.** The producer successor was
   assembled separately from the held #8648 admission harness. It keeps only
   the bounded producer, four Go claim slots, existing Redis/SQL fences,
   the Python database-only executor, and cold rollback. The large patch
   addressed specific crash and ownership failures; it is not a template for
   later family ports.
2. **Keep authority and reversal correct before traffic.** A selected posting
   must have exactly one schedule and one owner. The existing routing epoch
   must be monotonic across activation and rollback, including restored Redis
   or host snapshots. Cover lease loss, failed render/executor transitions,
   Redis persistence boundaries, pending-receipt reboot, and rollback from
  current PostgreSQL schedule truth. Bound task occupancy for the next cohort;
   do not build generic compaction or a new distributed scheduler for it.
3. **Dark default; c1 overlay is receipt guarded.** The merged revision passed real
   Redis/PostgreSQL transitions, container startup, and the required CI/deploy
   gates. The ordinary deploy lists the old Python/Chromium services and dark
   claimant. The c1 overlay has been cold-rolled back and reactivated around
   crawler deploys without changing its five retained due times. The current
   active epoch is 18 on revision `4a88deaf`. Check the
   current receipt, Redis owner, and host mutation lock before any mutation;
   [#8648](https://github.com/colophon-group/jobseek/issues/8648) records the
   current routing epoch. No reviewer or operator approval is an additional
   gate once the stated checks pass.
4. **Whole-lane fixture complete.** The merged harness exercised the producer
   with identical immutable fixture inputs and equal 1.5 GiB whole-lane
   budgets. Its report includes exact canonical output, terminal state,
   requests, queue conservation, paired CPU seconds, peak/retained RSS,
   elapsed time, and correct terminal URLs per GiB-minute. The measured
   density improvement and identical output meet the fixture decision rule;
   no arbitrary 1.25x hurdle or approval is required. Small live-source
   parity and production host telemetry remain part of c1 admission. Treat
   live-input drift as inconclusive.

   The held `fix-crawler/go-b0-admission` branch at `266725d45` is source
   material, not a merge candidate. It bypassed the Go producer via Python
   queue mutation. The merged harness retained its immutable fixture, PKI,
   output comparison, and external cgroup sampler, then fed the candidate
   through the Go producer's Unix socket. It counted the producer in the
   candidate cgroup and budget: 32 MiB producer,
   96 MiB supervisor, 384 MiB executor, 1024 MiB renderer (1536 MiB total),
   against a 1536 MiB control. The isolated counterbalanced c1/c4 report
   passed. This is fixture evidence; production c1 has not processed a due
   task yet.
5. **Observe c1, then expand to three origins if the numbers hold.** C1 has
   exclusive Go/Lightpanda ownership for one low-rate origin.
   Verify no duplicate origin request, stale write, lost schedule, unsupported
   capability, or same-task Chromium fallback. Expand to the other two
   currently validated origins only if c1 remains correct and the combined
   lane stays inside its memory and freshness limits. Judge complete scheduled
   work, exact output and queue effects, request conservation, CPU, and memory
   on the actual cohort. Record sample size and uncertainty; cold-rollback on
   a material regression. The
   fixed c4 benchmark manifest contains a `suspect` board. Production cutover
   uses a separate c3 manifest with only the three validated origins; c4 stays
   available to the frozen four-origin fixture and is rejected by the production
   activation wrapper. Never activate the suspect board just to reach four.

## Decision after B0

If the admitted multi-origin lane preserves output and uses fewer whole-lane
resources under the equal workload, choose one additional bounded family. The
Go HTTP sitemap worker already on `main` is the natural candidate, subject to
a stable-source cohort and the same exclusive ownership
and publisher-policy checks. Port only the semantics required by that cohort.
If the B0 result is weak or inconclusive, keep the working Python path and
resolve the measured limitation before expanding.

After B0, migrate one measured family or independently replaceable process at
a time: Go HTTP monitors and detail fetches, remaining monitor/scraper
families and enrichment, export/maintenance, then configuration sync.
For browser profiles, use pinned Lightpanda replay or move the origin to a
proved HTTP/API route. Track every temporary Chromium assignment until none
remain. Complete the migration only when the enabled fleet has zero Python or
Chromium crawl ownership, parity and resource measurements pass for its actual
workload, and the legacy production services and rollback window are retired.

Do not make the old broad issue list a dependency graph again. In particular,
defer catalog/outbox replacement, queue v2, dynamic sharding, generic browser
APIs, every ATS/monitor family, and Chromium retirement as *early pilot
prerequisites*. Revisit a Lightpanda capability when a specific configured
profile and pinned Linux build provide a concrete reason and exact replay
evidence. A new abstraction is justified only by a measured migration need.

## Issue ownership

- #7935: concise epic and the current decision record.
- #8738: completed implementation of exclusive Go B0 producer/ownership and
  cold reversal. #8881 is the merged dark foundation; #9874 merged the
  producer and deployed dark. Traffic admission remains #8648.
- #8648: whole-lane output parity and measured resource efficiency, followed
  by c1 observation and the validated three-origin expansion.
- #7941: cohort switch/reversal tracking folded into #8738; avoid a parallel
  generic cutover program.
- #7938: parked queue-v2 contract; it does not gate B0.
- #7962/#7963 and other family-port issues: deferred until measured cohort
  demand justifies a bounded slice.
- #7966: final zero-Python/Playwright/Chromium completion and retirement gate,
  reopened for the complete migration goal; it does not block B0 admission.
