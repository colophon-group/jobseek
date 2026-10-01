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

At the rich-only checkpoint, canonical observation required `active`; Python can legitimately move an enabled
board to `suspect`, `quarantined`, `gone_pending` or `gone` and continue recovery
polls. A retained member in those states would currently be rejected by native
claims and excluded by legacy. The lifecycle implementation below resolves that library gap.
Metadata publication must also preserve the full immutable claim snapshot through
terminal settlement/commit-before-ack recovery.

## Native Greenhouse lifecycle checkpoint, October 1

Implementation `8a6f1bc08724591ef544259070555d857ee88086` adds an opaque cycle
capturing PostgreSQL discovery start before fetch, counting only committed rich
chunks and retaining unique URL identities. A failed chunk prevents success/
absence finalization. Success, failure, provider-gone and reservation paths
commit their terminal effects and canonical deadline with the opaque attempt
receipt; queue settlement still validates that exact committed deadline.

Enabled `suspect`, `quarantined`, `gone_pending` and `gone` states remain eligible
for their installed native owner. Lifecycle baseline/confirmation state comes
from fresh locked PostgreSQL metadata; the full detached Redis claim snapshot
remains unchanged through settlement and crash recovery. Complete contractions
require three identical inventories/config fingerprints and at most 5,000
missing rows. Partial/filtered inventories retain the existing guard behavior;
truncated results skip absence. Empty runs retain six-confirmation delisting,
and generic failures retain recoverable five-strike quarantine/daily-capped due.

Provider disappearance requires the exact token API endpoint and HTTP 404,
spaced six-hour confirmations, a third confirmation after recent success, and
daily probes after a confirmed gone transition. Nonempty responses recover the
same selected board. Header reservations are recorded monotonically with their
resource/policy provenance; pre-existing reservations can finish without fetch.
Listing visibility and success/failure accounting are preserved. Native skips
persist a future PostgreSQL deadline with the receipt, intentionally closing
the legacy worker's Redis-only reservation skip schedule gap. Missing headers
never clear the flag. This is not complete publisher-policy or network proof.

The mandatory CI workflow regenerates 21 disappearance and 20 provider-gone
decision cases from the actual Python processor/policy. Three Python contract
tests bind the native rich expressions and nine lifecycle SQL statements.
Local PostgreSQL18.6 migration head/down0034/head passed; full native/private
Redis races passed in 23.724 seconds, production reaper races in 4.469 seconds,
12 ownership/SQL Python tests and native rich preparation races passed. Ten
lifecycle database test groups cover stale-cache repeated cycles, recovery,
partial/rejected effects, publisher propagation, actual terminal SQL rollback
retaining earlier chunks, and commit-before-ack recovery after a lifecycle
state change. Go vet/tidy/format, Ruff, workflow lint and 130 workflow/runtime/
version/documentation tests passed. Fixtures shut down and production was unchanged.
The [portable lifecycle evidence](evidence/go-ordinary-lifecycle-2026-10-01.json)
records exact source and remaining limits.

Prior checkpoint `01b7366f503a6145bf83dfe074282afa69ed78c8` passed both Linux
architectures in [run 36797683763](https://github.com/colophon-group/jobseek/actions/runs/36797683763)
and full CI in [run 36797708877](https://github.com/colophon-group/jobseek/actions/runs/36797708877).
Its B0 admission was still measuring; those earlier results do not validate this
implementation or authorize merging a draft. Fresh current-head checks remain
required. PR #10210 remains draft, v0.13.904 remains an unselected candidate,
and ordinary production workers remain Python.

## Next delivery and completion gates

Processing assembly `9d7a8f5a80b528597c0a1bcf3eeb593f5b24fa8b` now connects
the default non-streaming Greenhouse inventory to native preparation, owned
batches and terminal lifecycle in
[`go/ordinary-worker`](../apps/crawler/go/ordinary-worker/README.md).
It is a library, not the installed ordinary process. Forty URL and six inventory
cases are regenerated from Python's actual normalization/processing functions.
Raw duplicate content and first dictionary position are preserved before
canonical aliases choose the last raw dictionary entry; invalid/navigation
URLs are counted; truncation never slices collected postings.

The assembly validates inventory accounting and prepares each entire 500-row
chunk before SQL. Any failed or canceled preparation/persistence invalidates
success/absence authority on the opaque cycle, including failures outside the
posting callback. Real owned PostgreSQL/Redis assembly proof loads a private
reference snapshot and the native SQLite location index, derives fields for
1,001 postings across three batches, checks last-duplicate titles and exact
pending description/R2 bytes, guards removal of the unseen original, and
settles the canonical receipt deadline. A second real fixture fails preparation
after 500 committed rows, retains that prefix and original liveness, rejects
success/absence, and completes canonical failure scheduling.

Actual PostgreSQL18.6 migration reversal, full queue races (22.939 seconds),
assembled native processing races (13.268 seconds), production reaper races
(3.320 seconds), 12 ownership/SQL Python tests, native rich preparation races,
Go vet/tidy/format, Ruff, workflow lint and 130 repository checks passed locally.
Fixtures shut down. The [portable pipeline evidence](evidence/go-ordinary-pipeline-2026-10-01.json)
separates this source from earlier green checks. Previous lifecycle checkpoint
`7e1094733` passed Linux run 36800629362, full CI 36800624392 and B0 admission
36800629213; it remains draft and those checks do not cover later code.
Production is unchanged and ordinary workers remain Python.

### Native HTTP discovery continuation, October 1

Source `aa99863e3302b4709db463c848065002f74dcd4f` now performs the Greenhouse
monitor's single logical GET through a process-owned client and feeds the
owned 1,001-posting fixture from one real HTTP 202 inventory. Fifty-two actual
Python captures prove successful 2xx statuses, final-resource reservation
precedence over 404/status/JSON, joined duplicate headers, Unicode/Latin-1
header decoding, UTF-8/16/32 JSON byte detection, redirects and failure classes.
Every failure supplies no partial inventory. Read/cancel/body-limit failures
also supply no completed resource signal. A real HTTP redirect fixture
preserves cookies and client reuse.
Private response observations bind the final resource without becoming write
authority. Redirected provider/publisher lifecycle handling is still unselected.

The separate ordinary lookup store has one connection, its own application
attribution and a read-only PostgreSQL default. Native taxonomy/currency and
SQLite location preparation pass through it; an actual board update is rejected.
Full native assembly races passed in 15.394 seconds, reader/preparation races
in 3.069 seconds, and 39 runtime/version/documentation checks plus Go/Ruff/workflow
linters passed. Private fixtures shut down. All saved inventory/URL/HTTP oracles
remain byte-identical across Python hash seeds 1, 2 and 37.

The previous pipeline checkpoint `654ed153e` passed full CI 36831279197 and
B0 admission 36831284162. Linux run 36831284128 passed ARM64; AMD64's dependency
download timeout was retried and exposed only nondeterministic drop-count key
ordering in the capture. This source fixes that serialization. Fresh checks
are required; earlier greens do not cover this source or authorize a draft.
The [portable discovery evidence](evidence/go-ordinary-discovery-2026-10-01.json)
records source identity, the effective HTTP baseline and remaining gates.

Discovery checkpoint `c249e1742c24e10f6b50eceb935a648e333bd320` subsequently
passed both Linux architectures in run 36835150216, full CI in run 36835149192
and B0 admission in run 36835150489. The actual deploy gate rejects the draft;
these results do not cover the later transport source.

### Persistent verified direct transport, October 1

Source `593a7c9021e36076a20c7b61faa9f6ce393634b2` now prepares a reusable
verified HTTP/1.1 client against the actual pinned Python/httpx pool: 100
connections, 20 keepalive connections, five-second expiry, 20 redirects and
separate 30-second connect/read/write/pool deadlines. An explicit CA PEM bundle
is mandatory; startup freezes the internal-host allowlist. Public hostname
requests validate all DNS answers at every redirect and before reuse, then pin
new dials to validated literals. Temporary DNS errors have two bounded retries;
HTTP requests have no automatic retry, including failed reused GETs.

Process-owned cookies, explicit header overrides and encoded body accounting
are preserved. Detached race-safe observations conserve requests = responses +
no-response outcomes without turning a decode failure into another request.
Gzip/deflate decoding matches Python HTTPX captures, including complete HTTP
bodies missing compression trailers; raw HTTP truncation/cancellation still
fails. Encoded and decoded bodies have 64MiB limits requiring cohort admission.
233 actual Python address cases, nine DNS answer sets and 25 content-decoding
captures pass, alongside verified TLS/reuse, blocked redirect/rebinding,
request replay suppression, pool pressure, timeouts and cancellation fixtures.

The real 1,001-posting PostgreSQL/Redis assembly now receives its HTTP202
inventory through this native verified TLS transport, including encoded-byte
conservation. Full native assembly races passed in 24.672 seconds and reader/
preparation races in 3.357 seconds; 39 repository checks and linters passed.
Capture regeneration is byte-identical across hash seeds 1, 2 and 37. Private
fixtures shut down, and production is unchanged. The
[portable transport evidence](evidence/go-ordinary-direct-http-2026-10-01.json)
binds the runtime source, proof and remaining gates.

The native executable, protected CA/internal-host assets, production metric
attribution, shared host circuits, canonical deferrals and fenced learned-egress
publication remain outstanding. Bind final-resource provider/publisher outcomes
to the installed owner without mutating the inflight claim snapshot. Prove
startup/concurrency/heartbeat/drain, installed cancellation/crash/claim-loss,
all-writer cutover and shared-epoch cold reversal before selection. Production
ordinary workers remain Python; full profile/consumer, fleet/cost and retirement
gates remain in the continuation plan. The full migration goal remains active.

### Shared host circuits and durable deferrals, October 1

Source `8a84d82ca06597a254d675acf979a1bbe0bf500b` now prepares native host
preflight and outcomes using the existing shared Redis keys and byte-identical
Python failure/success Lua. Strict Greenhouse preflight uses the learned failure
host, then configured board hostname, preserving the Python fallback rather
than assuming the API hostname. Open circuits defer to their deadline; occupied
half-open leases defer to the next probe time. One bounded recovery probe remains
shared across workers. Native deferrals commit a future canonical PostgreSQL
deadline and receipt without changing board success/failure or listing state.

Failure completion records one circuit outcome for the actual run and retains
it across SQL rollback/retry, including ambiguous protective replies. Normal
board backoff and the available circuit lower bound commit together. Circuit
bounds round upward to PostgreSQL microseconds. Migration0037 adds a nullable
learned-host field to the completed attempt receipt; the inflight configuration
stays unchanged. Redis settlement publishes that field atomically after token-
guarded lease retirement, so the next claim sees new routing. A restarted
worker recovers and publishes the same host/due receipt without another failed
run. Mismatched receipt host, stale claim and retired epoch reject publication.
Native success receipts reset actual observed hosts without replaying completed
protective transitions. Circuit errors fail open while canonical attempt
checks remain mandatory; provider404/publisher outcomes remain separate.

Head0037/down0036/head0037 passed on owned PostgreSQL18.6. Full native queue races
passed in 17.140 seconds, assembly races in 22.135 seconds and reader/preparation
races in 3.276 seconds. All 185 mandatory legacy PostgreSQL/Redis Python tests
passed without skips; 39 repository checks and linters passed. Real fixtures
prove threshold/no-extension, 64 competing probe acquisitions with one winner,
canonical preflight, failure lower bounds, terminal rollback without circuit
replay, receipt mismatch/stale/epoch guards and commit-before-ack host recovery.
The verified TLS 1,001-posting assembly now recovers its API circuit; preparation
failure after a committed prefix publishes its fallback host only at settlement.
Private fixtures shut down and production is unchanged. See the
[portable circuit evidence](evidence/go-ordinary-host-circuit-2026-10-01.json).

This is a prepared library, requiring fresh candidate checks. It does not prove
the native executable, redirected provider/publisher lineage, protected startup
assets, installed process faults or all-writer cutover/cold reversal. Complete
those next, followed by all enabled profiles/consumers and fleet/cost/retirement
gates. Ordinary production workers remain Python and the full goal remains active.

On September 30 at 20:15:09 UTC, a read-only production census recorded 8,019 boards: 7,885
enabled and 134 disabled. Of enabled boards, 522 had stored browser requirements
(419 monitor, 264 scraper, overlapping); Greenhouse accounted for 2,280 boards,
741 naturally due, four with existing failures and none never successful.
Stored family/browser flags do not establish effective profile or native coverage.

Prioritize a bounded standard Greenhouse HTTP/API cohort after effective
configuration validation. Ownership/selection and rich batch persistence are
prepared, including full default inventory and terminal board lifecycle. Connect
that assembly to the native executable and
supported all-writer startup/projection/cutover while
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

### Complete claim-bound Greenhouse runner, October 1

Source `3e9aef091edcd8f1ab9f911c45b2f184bb2f465d` now connects the completed
native assembly through `RunGreenhouseClaim`. A sealed verified client prevents
callers replacing its transport or redirect policy. The runner requires an
ownership-bound opaque claim; every canonical effect still checks the fresh
installed plan/epoch/configuration. The initial token endpoint stays bound to
the claim while private completed fetch responses supply the final resource for
provider404 and publisher-policy lifecycle. Publisher headers take precedence
over status/JSON only after the complete verified response body is obtained.

Pre-existing reservations and circuit deferrals settle without fetch. Recovered
terminal receipts settle without fetching, preparing or replaying host outcomes.
A publisher reservation during preparation preserves committed prefix batches
and finalizes reservation without failure budget or absence. Cancellation never
manufactures terminal lifecycle authority. Result settlement is true only after
token-guarded acknowledgement; committed receipts remain recoverable on failure.

Production Python and Go reschedule Lua remain identical. Both now preflight all
used queue/index types and finite numeric inputs before mutations, preventing a
corrupt ready index from removing a lease or losing learned-host publication.
Thirteen real Redis fault cases compare exact queue/lease/cache snapshots.

The real TLS 1,001-posting fixture now runs through this complete runner. Eight
redirected response cases, two no-fetch cases, mid-body cancellation, reservation
after the first 500 postings, strict response-resource rejection and a true
commit-before-ack process-loss/reap/reclaim fixture passed. Recovery preserves
canonical due time and learned host without HTTP/CPU/circuit/failure replay.
Queue races passed in 26.414 seconds, assembly in 20.259 seconds and reader/
preparation in 2.875 seconds. Migration head/down0036/head, all 185 mandatory
legacy tests without skips, 39 repository checks and vet/format passed. Private
fixtures shut down and production is unchanged. See
[portable claim-run evidence](evidence/go-ordinary-claim-run-2026-10-01.json).

Circuit checkpoint `3684247cc` passed both Linux architectures (36846132716),
full CI (36846125648) and B0 admission (36846132352). Its actual deploy gate
rejects the draft; those checks do not cover this later source. Fresh checks and
the installed native executable remain required. Continue with protected startup,
metrics/concurrency/deadlines/heartbeat/drain, installed fault proof and supported
all-writer cutover/shared-epoch cold reversal, then every enabled profile/consumer,
fleet/freshness/queue/cost and rollback-window retirement gates. The full migration
goal remains active; ordinary production workers remain Python.

### Native ordinary process and image wiring, October 1

Source `2b775782f275a48b6a5890463bbfc9ac96b37d17` connects the claim runner to
`go-ordinary-worker`. Startup binds the binary's own clean VCS/linker revision to
the exact installed source, plan SHA256, projection SHA1 and routing epoch. It
cannot activate ownership, repair projections or adopt an allocator. Models,
reference/currency rows and SQLite locations load natively through the separate
read-only one-connection reader; ordinary write authority keeps its one-connection
budget. Certifi2026.2.25's CA snapshot and MPL notice are compiled without runtime
Python or system-store fallback. Trusted service/proxy/internal-host inputs freeze
before claims. Both images now install the binary with protected source-build
arguments; no native ordinary service is enabled by this change.

The process bounds active claims (default five) without extra prefetch. It renews
600-second leases every120 seconds under fresh ownership checks, enforces600-second
task deadlines and keeps task contexts/heartbeats live during30-second signal drain.
Expired drain cancels work without clearing recoverable leases or inventing terminal
state. Five-second cancellation grace also bounds uncooperative tasks; process exit
skips blocking cleanup when work remains. A claim-loop watchdog prevents a responsive
metrics listener masking a claim outage. Health probes verify installed source/plan/
epoch and live progress without another database connection or redirects.

Stable bounded task/duration/heartbeat/drain/posting and conserved origin/response/
transport-error/body-byte metrics preserve the replacement boundary. Native extraction
duration excludes posting persistence; no-fetch/recovered receipts do not emit another
extraction. Partial batch observations retain only confirmed committed counts without
whole-inventory authority. Metrics/errors expose no credentials or upstream URLs/hosts.

Real native command fixtures prove owned no-fetch publisher-cycle settlement, exact
build/CA/profile identity, health, signal drain and rejection of a wrong projection.
Real TLS token loss cancels a blocked fetch without canonical failure or fabricated
terminal receipt. Deadline/drain/heartbeat/acknowledgement/watchdog/uncooperative-task
race cases passed along with the existing1,001-posting rich/description/absence and
recovery fixtures. Queue races passed in 21.292 seconds, process/assembly in 42.679 and
reader/preparation in 15.437; these include parallel cross-build load, not comparative
performance. All185 mandatory legacy tests without skips,96 image/deployment tests,
130 repository checks, vet/tidy/format/actionlint and Linux AMD64/ARM64 cross-builds
passed. Private fixtures shut down; production is unchanged. See
[portable process evidence](evidence/go-ordinary-runtime-2026-10-01.json).

Prior claim-run checkpoint `426a6c47d` passed both Linux architectures (36851878686),
full CI (36851882626) and B0 admission (36851878654). Its actual gate refuses the
draft and those checks do not cover this later source. Prove the immutable installed
Linux worker and true SIGKILL/restart recovery, then supported all-writer ownership/
projection installation, shared B0 epoch cutover/cold reversal and effective profile
admission before native selection. Every enabled profile/runtime consumer, fleet/
freshness/queue/cost and actual rollback-window retirement remain required. Ordinary
production workers remain Python and the full migration goal remains active.

### Executable SIGKILL and installed-component admission, October 1

Source `ad8cc268f9a02ca72b646fb1fa1d3ad296991601` adds a real child-process
SIGKILL after terminal PostgreSQL commit and failed queue acknowledgement.
A private board-scoped trigger/advisory lock pauses the terminal transaction;
the fixture corrupts only its disposable ready index after claim and releases
commit. Guarded acknowledgement retains the lease token and board snapshot.
The fixture verifies the committed future due, completed receipt and unchanged
failure budget before attesting actual SIGKILL termination.

Production guarded reaping revokes the killed generation, and the same binary/
source/plan/epoch restarts with a new token. Its recovered receipt settles without
HTTP or native extraction, preserves complete canonical board/posting rows,
retained receipt contents, future due and failure budget, and leaves an unrelated
later host-circuit failure intact. Redis scheduling equals the canonical
microsecond deadline; identity health and final signal drain pass. Local full
worker races passed in 36.973 seconds, with 35 runtime/version repository checks,
vet/tidy/format/actionlint and private fixture cleanup. Production is unchanged.

The protected installed-image workflow now extracts the built binary and actual
model assets, checks the binary hash against the image, makes extracted assets
read-only and executes both native process fixtures against mandatory private
PostgreSQL17/Redis on Linux. It saves exact source, image ID, binary/asset hashes
and test logs. Execution is pending; this is installed-component proof on Linux,
not complete production-container/public-network admission. See
[portable crash evidence](evidence/go-ordinary-process-crash-2026-10-01.json).

Previous process checkpoint `c32640c66` passed Linux 36856484383, full CI 36856561101
and installed identity 36856620870. Its B0 run 36856484342 remains measuring;
actual deploy gate still refuses the draft. New-head checks remain required.
Continue with installed proof and the coordinated all-writer ordinary/B0
ownership protocol in the [continuation plan](27-go-lightpanda-continuation-plan.md),
then all enabled profiles/consumers, fleet/freshness/queue/cost and actual
rollback-window retirement. The full migration goal remains active.
