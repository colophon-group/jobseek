# Native ordinary worker authority checkpoint

The full [Go and Lightpanda migration plan](27-go-lightpanda-continuation-plan.md)
remains active. This checkpoint continues draft
[PR #10207](https://github.com/colophon-group/jobseek/pull/10207). It is an
unselected authority foundation, with no ordinary native executable or
production ownership change. Its
[module contract and next gates](../apps/crawler/go/ordinary-queue/README.md)
are the implementation handoff.

The latest merged production checkpoint is
[PR #10209](https://github.com/colophon-group/jobseek/pull/10209), merge
`8d5e17e94f949c8a5e35c53878c988b7d3ad4c9a`. Crawler v0.13.902 remains
`b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`, with the native B0 executor at
restored epoch 145. See the
[native B0 production checkpoint](29-native-go-b0-executor-checkpoint-2026-09-30.md)
for exact images, supported cold reversal and the first naturally due Bunq
commit. This entry makes no fresh host health attestation. Ordinary workers
remain Python; production Python/Playwright/Chromium retirement is incomplete.

## Prepared authority slice

The previous Redis attempt generation now binds monitor/detail activation,
canonical write transactions and settlement to a retained PostgreSQL fence.
The library reuses the current B0 global routing epoch and adds a shared lease
barrier so reaping cannot end native authority during a database transaction.
The production Go reaper participates; legacy direct reaping skips tokenized
attempts. Terminal receipts retain canonical deadlines, including unscheduled
details. A replacement attempt recovers unchanged committed output after a lost
acknowledgement, including across ownership retirement, without repeating effects.

Local race tests pass with real private Redis and fully migrated PostgreSQL
18.6. They verify delayed expired activation, callback/cancellation/configuration
rollback, commit ordered before epoch retirement, stale schema/write/queue
operations, exact deadlines, NULL detail completion, modeled commit-before-ack
recovery and the production Go reaper barrier. The actual migration passes
upgrade/downgrade/re-upgrade. Focused Python queue/reaper/pool checks pass
(130 tests); Go vet/module checks and workflow lint/security checks pass.
The workflow now requires real PostgreSQL 17 and Redis on Linux amd64 and arm64.
Exact-head CI results belong in the PR; earlier token-only runs do not prove the
new database authority. Candidate VERSION remains v0.13.903.

These fixtures prove authority ordering and atomic canonical test effects.
They do not prove native ordinary extraction/enrichment parity, an actual
ordinary process crash, installed ownership, full cold reversal or fleet cost.
Raw queue primitives are not database write authority. The `guarded` Lua argument
is a trusted wrapper convention, not a security capability.

## Admission interruption and prepared fix

At authority head `8b4db1eb69dd3418de512d14d58a533878cd8d2b`,
Required CI, installed runtime contracts and the real PostgreSQL/Redis jobs on
both Linux architectures passed. Whole-lane admission
[run 36778583149](https://github.com/colophon-group/jobseek/actions/runs/36778583149)
failed during the c1-p3 candidate's legacy schedule transfer, before its
supervisor started: the producer reported `corruption/activate`. Partial pairs
do not establish resource admission. The failed report is retained privately.

A private real Redis reproduction exposed a matching activation rejection:
`ZSCORE` returned `1.7908049340051439e+9`, which the fixed-decimal validator
rejected as corrupt legacy state. The failed CI artifact does not retain its raw
score, so this establishes a reproduced defect rather than the exact failed
arm's input. The prepared fix accepts bounded finite scientific notation and
retains the original score text for exact rollback. Malformed, negative,
overflowing and underflowing values still fail before mutation. Activation
diagnostics emit only reviewed enums, excluding raw configuration/reply/error
text. The new candidate Lua SHA256 is
`7c3b67b6b9eefdcf0dc9ae01f62a4d45f6ce0f67f8fa551dfd484dd4ce6fb59b`;
the production script identity in the preceding checkpoint remains historical.

Local race regressions transfer, audit and roll back 512 fractional schedules
using actual Redis, preserving their original scores, and reject malformed
guards without changing ready records. The 163 focused Python activation,
queue and producer-client tests pass. These regressions are included in the
required Linux CI installed producer test filter. Fresh exact-head Required CI,
installed runtime/authority tests and all 16 whole-lane arms subsequently passed
at `86813918a20a499c5d59a39ca8c33797161f8743`: CI run 36783332409, runtime
run 36783332427, both ordinary Linux architectures in run 36783332419 and
[admission run 36783332384](https://github.com/colophon-group/jobseek/actions/runs/36783332384).
Its [portable numeric evidence](evidence/go-ordinary-foundation-b0-admission-2026-10-01.json)
binds the tested merge checkout `db7ed34c48d125ca3f47c91348003a0893a4ff4a`
and exact fixture images. All eight candidate/control pairs match canonical,
per-task and source-response hashes; terminal/persisted/write counts, Redis
conservation and cleanup are exact. This is synthetic B0 admission with a
generated production-cardinality taxonomy, not fleet output or production
whole-service cost. PR #10207 remains a draft and unselected;
the draft's actual Crawler Deploy Gate is not merge or deployment authority.

## Canonical profile continuation

Stacked draft [PR #10210](https://github.com/colophon-group/jobseek/pull/10210)
prepares strict native Greenhouse token/skip eligibility and an authoritative
PostgreSQL/Redis readback. Implementation head
`e5ecd5d294a74e8553a8c14e93dc4dd21b2f9424` passed real migrated PostgreSQL 17
and private Redis on Linux amd64 and arm64 in
[run 36786462494](https://github.com/colophon-group/jobseek/actions/runs/36786462494).
Local migrated PostgreSQL 18.6/private Redis races verify effective settings,
stale projection/state/epoch rejection, and a delayed readback that waits for
a canonical update then rejects a newly disabled board. Observations change
no queue state and activate no write fence. Candidate VERSION is v0.13.904.

An eligibility observation grants no later authority. The next implementation
must bind the durable membership below to both native and legacy claims
atomically before any pop, with bounded progress past unselected heads/domains,
preserved global priority/fairness/rate/repair behavior and fail-closed handling
of missing/corrupt projection or complete Redis loss. Canonical status and ready
route membership require fresh validation at selection. Unsupported and
suspect/gone/quarantined profiles keep their current owner until native lifecycle
policy is proven. Native processing and installed crash/cold reversal follow.
The stacked PR automatically runs ordinary contracts/deploy-gate only; its final
retargeted runtime needs fresh Required CI, installed/admission and actual gate
proof. The parent B0 admission does not prove native ordinary execution.

## Durable ownership continuation

Implementation `6a71c4c9961ec8a4c0680b7906c985e92f8c0677` in draft
[PR #10210](https://github.com/colophon-group/jobseek/pull/10210) adds migration
0036 and an immutable staged/active/retired cohort document. Its exact payload
SHA256 binds the global epoch, source revision, supported member identities and
stable configuration hashes. Canonical staging rolls back a partial inventory
and changes no Redis state; exact active readback rejects stale source or epoch.
PostgreSQL retains retired identities and permits one active plan. Transitions
wait for ordinary lease transactions; readback avoids a row/barrier lock inversion.
Generic unbound Go claim/write/heartbeat/settlement operations reject an active
plan before queue mutations or callbacks.

The actual PostgreSQL 18.6 migration passed upgrade/down0034/re-upgrade, followed
by full PostgreSQL/private Redis races (15.575 seconds), production Go reaper
races (2.225 seconds), Go vet/module/format and migration Ruff checks. The 78
runtime/migration contract tests passed. Exact-source Linux PostgreSQL 17/private
Redis jobs passed on AMD64 and ARM64 in
[run 36790040524](https://github.com/colophon-group/jobseek/actions/runs/36790040524).
The [portable ownership evidence](evidence/go-ordinary-ownership-2026-10-01.json)
records identities, proof scope and remaining gates. Local fixture processes
and owned socket directories were cleaned up.

This document is not a queue or write grant. There is no production activation
endpoint, bound native claimant or processing executable yet. Legacy Python
claims are not changed by this slice. Before activation, install atomic selection
for both owners with exact startup/projection identities, missing/corrupt/full
Redis loss protection and full native processing through supported all-writer
cutover and cold reversal. Production remains unchanged and the full goal active.

## Atomic selection continuation

Implementation `f7ccc7b060e0e9100caae9d654c15d3f45891ca2` in draft
[PR #10210](https://github.com/colophon-group/jobseek/pull/10210) now binds native
owned authority and legacy claims to exact active plan/source/epoch identity.
The existing Lua queues filter before removal. Native claims validate an enabled
canonical profile under a row lock and compare the complete current Redis hash;
legacy excludes retained members even after config/domain/browser-route drift.
Bounded cohort batches and legacy domain/task cursors progress past foreign
heads. Global first-time priority, eight-monitor fairness, shared throttle,
scrape rotation and B0/native duplicate repair remain shared. Missing/corrupt
projection and complete Redis loss reject planned callers before effects.

Legacy pipeline startup requires all installed identity fields when a plan is
active. Every planned legacy claim reattests that plan under the database
lease/epoch barriers through the Lua pop. Native writes, heartbeat and settlement
also reject revoked canonical state, plan, epoch or projection before effects.
Unaware queue callers reject an installed projection. Supported cutover must
quiesce and replace every old writer before installation; there is no production
activation/projection writer or ordinary processing executable yet.

Exact committed source passed actual local PostgreSQL18.6 migration reversal,
full native/queue races (18.940 seconds), production Go reaper races (4.863 seconds)
and nine real Python ownership boundary tests. Pipeline/runtime/migration tests
(251), workflow/version/documentation tests (117), Go vet/module/format, Python
Ruff and workflow lint passed. Both Linux PostgreSQL17/private Redis architectures
passed native, reaper and mandatory legacy claim checks in
[run 36793700617](https://github.com/colophon-group/jobseek/actions/runs/36793700617).
The [portable selection evidence](evidence/go-ordinary-selection-2026-10-01.json)
records exact identities, scope and remaining gates. These are source fixtures,
not installed processing/image fault proof or fleet/whole-service cost evidence.

## Rich processing continuation — October 1

Source `8a5834e618e6ab75a42e91c898ec8811a322e35e` adds
`Processor.PrepareRichMonitor`, matching 18 captured cases from the actual Python
`_build_rich_new_records` and rich description staging path. Missing/garbage
titles remain valid rich records; locales retain their default; normalized and
trimmed description bytes/hash, language/coercion, salaries, experience,
technologies and taxonomy resolution match the fixture. Location inputs are
compared with a deterministic resolver; real location index/backfill evidence
remains in the earlier shared enrichment checkpoint. CI regenerates the oracle
from Python before replay, rejecting stale captures.

`Authority.WriteGreenhouseRichBatch` persists a prepared 1–500 posting chunk
under installed ownership and the exact canonical/config/lease fence. Frozen
Python URL diff, rich insert and description SQL retain ordered global posting
locks, first-owner attribution, active foreign liveness and inactive foreign
relisting. Rich refresh uses Python's replacement/NULL expressions, preserves
absent experience/taxonomy/technology derivations, and advances the existing CDC
trigger when content changes. Exact description bytes retain a completed R2
upload; changed bytes become pending without recording a detail scrape.
PostgreSQL 40P01 retries are bounded to three attempts with the existing jitter
budget. Detached nullable inputs keep enrichment/model dependencies out of the
shared exporter/reaper library.

Follow-up `449e58052ce52bac494436bfc6997c524d15fa29` rechecks a board's current
publisher reservation under the canonical row lock before any diff/content
effect, rejecting a reservation acquired during preparation. This does not yet
implement fetch-time publisher/transport policy or terminal reservation scheduling.

Local follow-up verification passed actual PostgreSQL18.6 migration reversal,
full native/private Redis races (18.756 seconds), production Go reaper races
(4.450 seconds), nine real legacy ownership and two SQL boundary tests, native
rich/detail preparation races, Go vet/module/format, Ruff, workflow lint and 117
workflow/version/documentation tests. Source `8a5834e61` passed both Linux
PostgreSQL17/private Redis jobs in
[run 36796933484](https://github.com/colophon-group/jobseek/actions/runs/36796933484).
That Linux run precedes the publisher follow-up. Current-head Linux, full required,
installed and admission results must be refreshed before any promotion. The
[portable rich evidence](evidence/go-ordinary-rich-2026-10-01.json) records this
distinction. Production remains unchanged and ordinary workers remain Python.

This API is nonterminal: it provides no complete-inventory receipt, absence/drop/
empty decision, board metadata/schedule update or queue acknowledgement. Unlike
Python's separate classification and rich write transactions, the native chunk
commits those effects atomically; full-cycle failure/recovery proof must cover
that boundary. The native executable, capture/filter/completeness pipeline,
failure/circuit/publisher policy and installed fault/cold-reversal evidence remain
required. No current fixture proves whole-inventory or fleet parity.

Before terminal integration, extend/prove owned lifecycle states. Canonical
observation currently requires `active`; Python can legitimately move an enabled
board to `suspect`, `quarantined`, `gone_pending` or `gone` and continue recovery
polls. A retained member in those states would currently be rejected by native
claims and excluded by legacy. Never activate this incomplete state contract.
Metadata publication must also preserve the full immutable claim snapshot through
terminal settlement/commit-before-ack recovery.

## Next delivery and completion gates

At 20:15:09 UTC, a read-only production census recorded 8,019 boards: 7,885
enabled and 134 disabled. Of enabled boards, 522 had stored browser requirements
(419 monitor, 264 scraper, overlapping); Greenhouse accounted for 2,280 boards,
741 naturally due, four with existing failures and none never successful.
Stored family/browser flags do not establish effective profile or native coverage.

Prioritize a bounded standard Greenhouse HTTP/API cohort after effective
configuration validation. Ownership/selection and rich batch persistence are
prepared. Connect native preparation/batches to complete inventory/lifecycle
processing, the native executable and supported all-writer startup/projection/cutover while
preserving unselected ready work, producer/repair/deferred/never-successful
behavior and the shared B0 epoch. Implement native monitor/detail fetch,
enrichment, canonical persistence,
complete/truncated inventories, disappearance, retry/circuit/publisher policy,
description deduplication/R2 and database-owned scheduling. Reuse the existing
Go Greenhouse parser and shared native enrichment/persistence.

Before selecting that cohort, prove installed process cancellation and actual
commit-before-ack crash recovery; supported all-writer quiesced cutover and cold
reversal; exact image/output/freshness/queue evidence; and fresh required CI plus
the actual Crawler Deploy Gate. Coordinate B0 and ordinary ownership because
retiring their shared global epoch invalidates both. Never adopt the allocator's
latest value without a verified active ownership plan.

Expand to every enabled effective profile, including suspect/gone/quarantined
rows, then replace remaining Python scheduling/maintenance/deployment consumers.
Measure comparable whole-service CPU/RAM/density/attributable cost and complete
the actual rollback window before removing production Python, Playwright,
Chromium and legacy runtime-only assets. Preserve useful isolated offline Python.
A first cohort or this authority library does not complete the migration goal.
