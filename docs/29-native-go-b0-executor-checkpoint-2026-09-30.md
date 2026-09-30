# Native Go B0 database executor checkpoint

Continuation of the [full migration plan](27-go-lightpanda-continuation-plan.md)
and [promoted location checkpoint](28-go-location-resolver-checkpoint-2026-09-30.md).
The full migration goal remains active. Native [PR #10204](https://github.com/colophon-group/jobseek/pull/10204)
merged and promoted crawler v0.13.902 at
`b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`. The active cdom database executor
is Go at restored epoch 145. Supported native cold reversal and the first
naturally due native fenced commit are recorded in the latest production
section below. Ordinary workers and remaining browser profiles still require
migration; whole-service resource/cost and final retirement remain incomplete.

## Prior location production boundary

Location PR #10189 and full deployment 36719644632 promoted v0.13.901 at
`ec906f05cb5c481ea988c9d8dd5ffdac29ee1454`. Supported cold retirement at epoch
140 and restoration at epoch 141 are recorded in the linked checkpoint.
The portable production evidence and updated checkpoint merged in
[PR #10202](https://github.com/colophon-group/jobseek/pull/10202) as
`1222fbe5d24a8cfd0591382ff771cb1b613b1192`.

At 14:07 UTC, all metric collectors responded, the host retained that exact
release and epoch 141, and no fresh B0 render/commit was observed. Ordinary Go
location calls are observed; canonical fresh B0 output remains pending natural
processing. Do not force due times or refetch publisher pages to manufacture
that evidence. Re-read current receipts, release identities and holds before
any subsequent mutation. A fresh read-only runtime collector at 15:41:59 UTC
confirmed the same immutable v0.13.901 release, epoch 141 and healthy ordinary
and B0 services; the native candidate remains unselected. At 16:32:27 UTC,
telemetry recorded three natural successful B0 renders/commits with no failures.
A read-only canonical database snapshot at 16:34:10 UTC found those three fresh
epoch-141 postings on Browser Use and Bunq, active with zero failures and
parallel location ID/type arrays. Location arrays, titles and description hashes
matched the prior stored rows; raw new render inputs were not retained for an
independent location replay. This is the deployed Go location bridge behind
the Python executor, not native ownership. See
[portable natural evidence](evidence/go-location-natural-b0-2026-09-30.json).

## Implemented candidate

The native module at `apps/crawler/go/lightpanda-b0-executor` preserves the
existing four-task/private-UDS conversation and reserved health slot. It has
no Redis or renderer authority credential consumer. It checks canonical task
identity and bounded runtime-v1 results before asking the supervisor for an
advancing write lease. The supervisor retains its lease mutex until commit
acknowledgement. Database activation remains a separate short transaction;
authoritative posting, description and schedule effects share require/write/
revoke in one transaction through one PostgreSQL connection.

It rechecks the mutable posting/board/parser assignment after activation.
Deleted, unscheduled and reserved content keep the existing skip behavior.
Verified rendered HTML feeds the existing Go DOM/JSON-LD packages directly,
with no origin request or parser subprocess. Gone redirects precede challenges;
HTTP 404/410, budget failures and transient failures retain distinct visibility
and scheduling behavior. The shared retry counter increments on transient
failures too; this migration preserves the frozen SQL rather than inferring
behavior from older comments.

Native taxonomy/rate loading uses the existing pool. Location core loading and
500-key backfill populate a private 0700-directory/0600-file SQLite index with
serialized mutable resolution. Scalar coercion, ordinary content and selected
enrichment preserve NULL/COALESCE, independent title matching, internship
overrides, monitor-field retention and empty-row backfill. Description bytes
retain the existing signed SHA-256-prefix hash, locale, deduplication and R2
pending-upload effects. Empty or malformed extraction remains transient.

The canonical task codec is shared with the Go producer/supervisor without
importing parser/database packages into those services. Frozen Python number
and string cases corrected float exponent thresholds and DEL escaping; this
shared runtime change requires fresh admission. The candidate bumps VERSION.

Startup validates exact mode, socket/shard/epoch, one-connection configuration
and forbidden authority credentials. The health command uses the reserved route
conversation, opening no additional database connection. Slow first-frame
classification is capped at 16 sockets. Normal shutdown gives admitted commits
15 seconds; a failed grace returns for process termination without waiting
indefinitely for resource cleanup. Installed fault proof remains required.

## Verification completed locally

- Frozen Python protocol, canonical JSON/task, rendered manifest, description
  hash and seven SQL-query checks pass.
- Both existing frozen DOM and JSON-LD parser corpora pass through the direct
  native rendered path, including parser errors and classification rules.
- Fourteen Python processing transaction cases pass for ordinary and selected
  enrichment, backfill, internship, language/NULL gates and empty results.
- The existing supervisor suite and shared contract package passed after codec
  extraction and canonical JSON corrections.
- Real isolated PostgreSQL 18.6 tests with all repository migrations pass for
  fence rejection, rollback/cancellation, duplicate claims, field codecs,
  description deduplication, failure/never-rescrape schedules, mutable identity,
  native location/rate loading and private index cleanup.
- Socket conversations against that database pass for native canonical writes
  and schedule acknowledgement, invalid authorization, revoked duplicates,
  failure classes, reserved content and unscheduled skips. Their processing
  fixtures do not replace a production taxonomy/whole-lane replay.
- Go race/vet/module/format checks, Linux ARM64 compilation and the offline
  Python oracle lint/format checks pass. CI workflow contract tests pass (91).

Required CI now includes a Linux PostgreSQL 17 native-executor job. The candidate
Docker build runs native unit contracts and installs the binary. At candidate
head `44660f03c5dd8af6132bcdfc4466ea4af3cf36e3`, Required CI run
36729181871 and installed runtime contracts run 36729181987 passed, including
real Linux PostgreSQL 17 native tests. Those installed contracts do not yet
exercise native process ownership. The installed-executable test passed
in CI at head `b47394e6d486e75cf743c95a3a85c8716729ad20`: native job
109946247296 in Required CI run 36732557873 passed, including the installed
executable step. It uses UID 10001, a 64-descriptor limit, real peer credentials, native
taxonomy startup, one PostgreSQL connection, four held tasks plus health, lost
acknowledgement followed by a hard kill/restart, duplicate rejection, stale
epoch rejection and bounded cleanup. It models a fresh private tmpfs on restart;
container cgroup and read-only filesystem proof remains separate. Its lost-ack
case withholds an already-generated acknowledgement from the supervisor; it
does not prove death before acknowledgement generation or Redis conservation.
The next candidate changes the synthetic ARM64 admission executor command and
health check to Go, retaining the same 384 MiB/one-CPU/read-only/private-tmpfs
container contract. Admission checks live executable identities, UID 10001 and
the 64-descriptor limit before and after collection, and rejects missing or
Python owner evidence. Native whole-lane run 36735074058 subsequently passed all 16 arms and eight
paired comparisons at PR head `1bae9e79528aaab97c1540bb536798f740d9549d`,
tested merge checkout `b0d5e99cdac7db30653aa4c1dc4e305e13dd44ca`.
[Portable synthetic evidence](evidence/go-native-b0-synthetic-2026-09-30.json)
records canonical hashes, exact queues/metrics/cleanup, live Go ownership,
container envelopes and numeric resources. The candidate had roughly
55–69 MiB sampled aggregate peaks; CPU and latency comparisons passed. These
are synthetic fixture results after startup, not production-taxonomy, startup
resource or attributable-cost proof. The subsequent policy/crash changes need
fresh admission.
These fixture results do not establish production write ownership.

## Fault and publisher-policy evidence

A stronger installed fixture is prepared after the passing lost-ack test. It
holds description persistence, queues an exclusive posting-table lock, then
releases persistence. The queued lock acquires after the native transaction
commits and blocks its post-commit schedule read. The fixture verifies durable
canonical content, observes the blocked read, and kills the real owner before
it can generate an acknowledgement. Its first Linux execution at head
`1bae9e79528aaab97c1540bb536798f740d9549d` failed at the description barrier:
the driver polls activity inside a transaction, which can retain a statistics
snapshot. The fixture now clears that snapshot before polling; its corrected
Linux execution passed in native job 109970327719 of Required CI run
36739532630 at PR head `3ba0da038cba239250052b7830b45ed6aee39f2a`.
The installed executable was killed after durable commit while its schedule
read was blocked, before acknowledgement generation. Restart rejected the same
claim and retained the committed schedule. This uses only isolated migrated
fixture tables, with no production fault hook or publisher request. It does
not replace Redis-linked fault or production recovery proof.

The held-result policy audit identified an existing B0 boundary gap in both
executors: `src/lightpanda/runtime.py` validates the HTML manifest and invokes
raw Go parsers without the shared header/meta reservation check.
Before this candidate, `BrowserSuccess` carried no main-document response
headers and the renderer discarded CDP response headers. The native direct
parser originally followed that path. Stored `tdm_reserved` skips alone did
not prove detection or persistence of a fresh resource signal.

The candidate implements that boundary with additive optional
`BrowserSuccess.resource_policy` signals. The renderer captures only bounded
TDM header values from its correlated main-frame/loader/URL snapshot. The native
executor requires signal-message presence before parsing; the fallback Python
path accepts the optional field and runs its existing shared check. An empty
present message means header inspection, and absence means unknown coverage.
The supervisor checks effective signals before a challenge retry.

A shared Go package matches 21 frozen Python header/meta cases, including
Unicode excerpt bounds, extra/duplicate attributes, entities, script/style/
comment literals, last-valid metadata and metadata precedence. Native
PostgreSQL tests cover fenced reservation persistence without title/description/
visibility changes, opt-in precedence, malformed/missing-evidence rejection,
duplicate rejection and schedule exclusion. Existing reserved/unscheduled
native skips now revoke their fence. A final locked reservation check prevents
content persistence when a reservation wins during processing.

Local validation passed real isolated PostgreSQL race tests, 75 Python B0
tests, generated-binding stability, contract/supervisor/renderer unit suites,
and cross-compiled Linux integration/vet. The native policy and corrected
installed crash tests also passed on Linux PostgreSQL 17 in job 109970327719.
The renderer image build initially omitted the new shared policy package from
its selective contract copy; the build context is corrected and compiles for
Linux ARM64. Actual renderer header correlation
and the complete new installed image/lane remain pending remote Linux evidence.
Strict deployed consumers reject unknown protobuf fields, so release the new
crawler and renderer through supported cold transitions before restoring B0;
there is no live backward-compatibility claim for an old consumer receiving
these signals. Before native ownership promotion, prove the exact candidate
across those installed boundaries. Preserve listing visibility, facts and
retained-description display, and enforce existing downstream mining gates.
Use synthetic entry-point cases, including signal-free, reserved, HTML opt-in,
malformed manifest, redirect correlation and rollback/revocation cases. Do not
claim complete origin-file/protocol coverage: the broader existing work remains
in issue #10090 and its documented scope. This audit authorizes no blanket
source exclusions or new origin traffic.

## Startup resource proof in progress

The corrected renderer image and pinned integration suites passed on ARM64 and
AMD64 at PR head `a990177d2363dae33c10e9553bcbcce683dfc36b`: renderer run
36740886647, pilot run 36740886649. Required CI run 36740886650 and installed
runtime contracts run 36740886648 also passed. Native whole-lane run
36740886561 subsequently passed all 16 arms/eight pairs at tested merge source
`8eb6a570b845bf14e126aa1b4b335f21da01be4a`. Its complete artifact is retained,
and [portable policy-aware synthetic evidence](evidence/go-native-b0-policy-synthetic-2026-09-30.json)
records identities, outputs, queues, resources and explicit measurement limits.
That run predates the new lifetime/cardinality/health-cadence checks.

A read-only production startup-taxonomy snapshot at 16:08:46 UTC contains
37,526 locations, 143,004 names in the six startup locales, 186 technologies,
91 occupations, nine seniorities and 31 currency rates. The complete raw
snapshot stays private. The optional isolated native test restored those rows
to local PostgreSQL 18.6 and loaded the real native lookups/index in 1,347 ms.
Its private index held 118,206 deduplicated name pairs and 37,526 English display
names in 7,806,976 bytes (7.45 MiB), kept one PostgreSQL connection, resolved
the fixed geographic case and cleaned up its owned files.
[Portable numeric evidence](evidence/go-native-b0-startup-taxonomy-2026-09-30.json)
binds the snapshot hash and scope. This local loader result excludes Linux
installed-image CPU/RAM, decoding/import overhead and transient tmpfs peaks.

The next admission change records cgroup CPU from container creation through
its final read and the prefix before post-startup sampling. It preserves
synchronized post-startup memory sampling for density and separately records
the sum of service lifetime peaks as a conservative aggregate upper bound.
It rejects missing/inconsistent lifetime counters, startup OOM/swap evidence
or an upper bound reaching the lane envelope. This sum is never represented
as a simultaneous peak or used as a density denominator.

The next fixture also matches actual production worker health cadence instead
of the historical one-second comparator probes. Actual installed intervals
are attested, so earlier synthetic CPU results are not production cost proof.

Both disposable lanes also receive a generated startup taxonomy with the
observed production row counts before their due time is set. The fixture
census must match in every arm. Generated names/slugs are synthetic and do not
establish production-taxonomy semantic parity or production cost. Local real
PostgreSQL census, adversarial numeric admission checks and native snapshot
loading pass; exact Linux admission of these new checks remains pending.

## Redis-linked recovery candidate

At exact head `e79622cf01e0da3d3a736617b5c71c4667872f1e`, Required CI run
36745802853, installed runtime contracts run 36745802507, renderer image run
36745802510 and both architecture pilot jobs in 36745802469 passed. Native
Linux PostgreSQL 17 job 109991854551 also passed the actual pre-acknowledgement
crash case. That verified case has no Redis-linked recovery assertion.
Startup-aware admission run 36745802814 passed at 17:12:59 UTC; its retained
artifact and portable result are recorded below.

The next test-only change links the installed crash to real dedicated Redis 8
through the existing reviewed Lua ABI, whose source SHA-256 is
`60bc7169651d3e8cc7abfcff6dec799170fa298539904a78b7f865534a3a2803`.
Only the isolated fixture driver receives Redis access; the native runtime and
same-UID client receive none. It claims and advances the real lease before
commit, checks inflight conservation after the kill, applies the supervisor's
EOF failure settlement, acquires a new token, rejects stale completion and
retries the same held result. It compares the native acknowledgement, PostgreSQL
schedule and Redis next-ready score exactly. Canonical content, description
hash/timestamp and a completed upload must survive that retry and duplicates.
This driver models the supervisor's settlement; it does not execute the actual
supervisor or prove a publisher-free production retry.

A separate real Redis expiry case passed locally under the race detector with
isolated PostgreSQL 18.6: expired heartbeat rejected, reaping conserves the
record, a new claim gets a new token, stale completion is rejected, and exact
rescheduling restores one ready record with no origin holder or inflight work.
Both cases passed in Linux native job 110006072585 of Required CI run
36749958480 at PR head `66bda1eff35c590eae5161b0ae146db11f27b82a`, using
PostgreSQL 17 and the pinned Redis 8 service's dedicated loopback database 15.
That job verifies actual installed UID10001 commit, death before acknowledgement,
restart, real Lua lease/failure/recovery conservation, exact DB/Redis schedules,
stale completion rejection and retained canonical/description upload effects.
These are operational fixtures, not supported cold reversal or native production
ownership. The subsequent overlay selection still requires fresh exact-head CI.

## Startup-aware admission passed; native selection prepared

[Whole-lane run 36745802814](https://github.com/colophon-group/jobseek/actions/runs/36745802814)
passed all 16 arms/eight pairs on ARM64 at PR head
`e79622cf01e0da3d3a736617b5c71c4667872f1e`, tested merge checkout
`dd648a01124b2ceef62cebaa9d076b5a32ba8d5e`. The retained complete artifact is
bound by hash in [portable startup evidence](evidence/go-native-b0-startup-synthetic-2026-09-30.json).
Every arm matched the generated 37,526-location/143,004-name census, canonical
output, queue/metrics and cleanup checks. No startup or sampled OOM/swap event
or service restart occurred. Native executable/UID, read-only root, private
32 MiB tmpfs and descriptor limits were attested.

The native lane used 3.918–4.619 CPU seconds over its measured container
lifetime versus 5.730–10.195 for the control; its startup prefix was included.
Synchronized post-startup memory and latency yielded density ratios of
5.55–11.70. Service lifetime peak sums are recorded only as conservative upper
bounds, not simultaneous memory peaks. All bounds fit the lane envelope. This
uses generated production cardinalities and actual production health cadence;
it does not establish production-taxonomy semantics, support-service costs,
attributable operating cost or the final whole-service resource gate.

The candidate production activation overlay now selects the native binary and
its reserved-socket health command, keeping the one-connection/384 MiB/one-CPU
contract. Its 288 MiB Go memory limit and complete health configuration match
the measured lane. The base ordinary workers still select Python. This pending
configuration is not installed on the host and needs fresh exact-head CI and
installed Redis-linked recovery. Native selection will follow the supported
coordinated cold crawler/renderer release and current deployment authority;
legacy release generations remain the supported complete reversal artifacts.
Observe natural canonical output, schedules, freshness and resources after
restoration, then continue ordinary worker ownership and enabled profiles.

## Installed image startup fixture repair

Final selection head `9cc1fd3dbcf07f947878b52d3abec78ca98c7516` passed the
native PostgreSQL/Redis job 110009685227 in Required CI run 36750890164.
The image lifecycle job 110009685009 failed because its freshly migrated
fixture lacks production reference tables: native startup reported missing
`technology`. Production imports these tables separately; Alembic does not
populate them. The candidate adds a small explicit reference-table SQL fixture
before image volume/health checks and exercises those exact SQL bytes with
native lookup/location loading in an owned local PostgreSQL schema. It keeps
the production native command and health selection. Fresh Required CI and image
lifecycle proof remain required after this test-only repair; no host mutation
or native ownership has occurred.

## Pre-promotion delivery gates (historical)

1. Complete installed-binary/image tests with real Linux UID isolation, private
   sockets, native taxonomy startup, the one-connection budget, four tasks plus
   health, read-only filesystem/tmpfs limits and bounded startup/shutdown.
2. Prove crash after commit before acknowledgement, lost/changed authorization,
   stale/current route changes, connection/CPU pressure and quiesced cold
   reversal. Audit publisher-policy/reserved-content coverage across the held
   renderer result and HTTP/browser routes without adding origin traffic.
3. Pass exact-candidate Required CI, installed runtime contracts, whole-lane
   admission and Crawler Deploy Gate. Keep production selection Python until
   all ownership gates pass. Refresh base/head authority and deployment holds
   immediately before any merge or mutation.
4. Select native B0 through the supported full release/cold wrapper, observe
   natural tasks and compare canonical fields, description/R2 effects,
   database-owned schedules, queue conservation and freshness. Measure the
   complete comparable lane, including startup and every consumer.
5. Continue native ordinary worker ownership, enabled-profile reconciliation,
   remaining runtime maintenance consumers and full resource/cutover/reversal
   proof. Retire production Python/Playwright/Chromium after replacement
   authority and the rollback window; retain useful isolated offline Python.


## Native selection merged and promoted

Native [PR #10204](https://github.com/colophon-group/jobseek/pull/10204)
merged at 18:35:34 UTC as `b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`,
version 0.13.902, bound to tested head
`ae6e1ec871ad347e9382988c97a662d113946055`. Required CI 36753632378,
installed runtime contracts 36753632490, renderer build 36753632421 and both
architecture pilots 36753632468 passed. Actual `Crawler Deploy Gate` was green
at run 36758382688 before merge. Main drift through PRs #10205/#10206 was
confined to `apps/web`; crawler, deployment and data trees were unchanged.
Fresh exact PR/head/base/main checks found no holds or conflicting reviews.

[Final whole-lane run 36753632374](https://github.com/colophon-group/jobseek/actions/runs/36753632374)
passed all 16 arms/eight pairs at tested merge checkout
`fcbad6c2c67753026eb10f4f367cf0df1826f6c3`. The complete retained report
SHA-256 is `266c07f9b93d897236eccb7d64d53b26373bc2a41fd9d0b69602726fca668841`;
[portable numeric evidence](evidence/go-native-b0-selection-synthetic-2026-09-30.json)
records exact canonical/queue/metrics/cleanup checks, generated startup census,
actual health cadence and process/container bounds. Native lifetime CPU was
4.007–4.847 seconds versus control 5.966–10.967; density ratios were 5.86–14.69.
These remain synthetic lane results with the same limits stated above.

The supported cold rollback initially refused a held mutation lock without
changes. After the existing holders finished, the wrapper completed retirement
at epoch 142: zero inflight records required settlement, 20 ready records were
restored, one terminal record was dropped under existing policy and no write
fences remained. Ordinary services passed readiness. The verified selector
helper cleared all 25 exact selectors against promoted v0.13.901/ec906f05.
No due time was forced and no publisher refetch was used for this evidence.

Crawler release [36759680070](https://github.com/colophon-group/jobseek/actions/runs/36759680070)
completed including promotion; exact-main renderer release
[36759767703](https://github.com/colophon-group/jobseek/actions/runs/36759767703)
also completed. Both use `b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`.

| Promoted artifact | Immutable identity |
| --- | --- |
| Crawler | `ghcr.io/colophon-group/jobseek-crawler@sha256:ccb1fb7d83fedd95fc428ea174730c85021d587e20660c451a55e9a04cefaa09` |
| Browser fallback | `ghcr.io/colophon-group/jobseek-crawler-browser@sha256:8b60691ff6a62b174f9b5009e4aa0aebcb2c0e6ab1d40e8f482c80a8c77ac36a` |
| Renderer | `ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:a4c925d28c466b23a90850637c8344825823b6e6f5139952699a8bee21f3f719` |

After exact host promotion readback, the verified helper staged 25 selectors
and the supported wrapper activated the remaining 20 selected records at epoch
143. Actual process readback found one `go-lightpanda-b0-executor`, UID10001,
64-descriptor limit, and one connection with application name
`jobseek:crawler:lightpanda-b0-executor:local`. This establishes native ownership.

Supported native cold reversal retired epoch 143 to 144, restored all 20 ready
records, dropped none, settled no inflight work and left no write fences.
Cold readback found zero native processes and zero native database connections.
All 140 sampled Browser Use/Bunq/Algorized canonical rows were unchanged across
that first activation/reversal. The same promoted release was restored through
the wrapper at epoch 145. At 19:13:55 UTC, all collectors responded, required
services passed readiness, and one actual Go owner and one native DB connection
were present. Its cgroup current memory was 41,209,856 bytes below the
402,653,184-byte limit, with zero OOM events. Production `memory.peak` is
unavailable; this component observation is not peak, savings or cost proof.

A naturally due Bunq posting committed through a Go-owned epoch-145 fence at
19:12:11 UTC during restoration readiness, before publication of the final
activation receipt. Telemetry subsequently showed one successful render and
one executor commit, zero failures and no inflight work. The posting remained
active with zero scrape failures. Titles, locales, nullable location fields,
description hash and description timestamp matched the pre-activation row.
The 3,044 stored UTF-8 bytes recomputed to the exact signed SHA-256-prefix hash;
its completed R2 upload remained intact. Its next scrape is exactly 24 hours
later, and read-only Redis inspection found the exact PostgreSQL deadline in
both the ready record and ZSET score, no lease, zero task failures, 20 ready,
zero inflight and zero dead records. No due time was forced or publisher
refetch used to manufacture this evidence.

[Portable native production proof](evidence/go-native-b0-production-2026-09-30.json)
records these observations and their limits. Current held render input was not
retained for independent extraction replay; one natural posting is not whole-fleet
output/freshness proof. The cold test retires/restores native authority within
the same release; it is not an older-image downgrade or injected production
fault. Full service cost, remaining consumers and final retirement remain open.

The independent ordinary queue foundation is saved in draft
[PR #10207](https://github.com/colophon-group/jobseek/pull/10207), now rebased on
that merged main. Its existing-Lua queue contracts passed required real private
Redis tests on Linux AMD64 and ARM64 in run 36756952939 before rebasing, then
again in run 36759941552 at rebased head
`f8ec4e9a4a3b2a8e555eb84795bc2ac16c055c99`. Required CI and runtime contract
results must still be read before a future merge. It selects no ordinary worker and establishes
no claim/write fence. Its README names the next owner/generation, settlement,
reaper, transactional monitor/detail and exclusive-profile gates. The full
migration goal remains active and incomplete.


## Current continuation

Continue the ordinary Go slice in draft PR #10207 while normal native B0 tasks
supply further output/failure/freshness evidence. Complete a bounded generation
extension across the existing claim, heartbeat, settlement and reaper state
machine, then transactional monitor/detail authority and exclusive effective
profile selection. Prove expiry/new-claim/stale settlement, cancelled writes,
commit-before-settlement recovery and route retirement; a Redis read or boolean
heartbeat is not a PostgreSQL fence. Reuse existing Go HTTP/API parsers and the
native enrichment/store instead of recreating them or creating another queue.

Reconcile every enabled effective profile and runtime consumer against current
production and CSV/registry state. The offline candidate census counted 522
browser-capable boards across 313 configured records and 17 registry records;
it includes all 7,885 CSV rows and is not a live enabled denominator or migrated
coverage percentage. Full fleet coverage, comparable whole-lane efficiency and
attributable cost, complete final cutover/reversal and the actual rollback
observation window precede production Python/Playwright/Chromium removal.
Retain useful separately packaged offline Python tools and frozen oracles.
