# Go and Lightpanda migration delivery plan

## Current checkpoint — October 31, 2026

**Goal active:** deliver the full production crawler migration to Go and
self-hosted Lightpanda or proven Go HTTP/API routes. Preserve every enabled board
and useful output. Establish better whole-lane efficiency without material
throughput/freshness regression, exercise supported reversal, then retire
reasonable legacy-only consumers. Preserve useful isolated offline Python.
This continues [plan #10185](https://github.com/colophon-group/jobseek/pull/10185)
and [salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177).
A Python-free processing image alone does not close the goal.

Installed **0.13.1136**, independently verified October 31 at 10:19 UTC:

| Identity | Value |
| --- | --- |
| Source and installed data revision | `c9140316abcf22e5cc6b17316af35c4290c0adba` |
| Native image | `sha256:a79527fac779baa8207271560c3cf6ff4d9468d376be0cac40ea59658f30e7ec` |
| Runtime contract | `fa15a1375baf6a7958e48af69cea894f316b95777f03d0b54a8d08606f85a9a8` |
| Data contract | `41db9b8ef808b62589f09b4a4407a56155f45fe06fb5a1d0c172da4a1bd66693` |
| Fleet | 8,151 enabled boards; schema 0041; default Go epoch 404; zero active migration plans |

[PR #11073](https://github.com/colophon-group/jobseek/pull/11073) merged after
all exact-head checks passed and a fresh state/main/review/comment/hold/rules
audit. The [supported full rollout and promotion](https://github.com/colophon-group/jobseek/actions/runs/41191236685)
succeeded. Independent reads verify all four Go actors on the exact image with
zero restarts/OOMs and unchanged budgets. No legacy processor runs; the
processing image exposes no Python, uv, Playwright or Chromium commands.

All 8,151 monitors remain represented: 8,126 queued and 25 valid leases, with
zero missing, duplicate, expired-only, token-presence or cache gaps. At 10:19,
2,386 monitors succeeded and 2,462 were checked in the preceding hour; 4,986 are
due and none is overdue a day. These bounded observations do not establish
universal freshness or a cleared detail backlog.

The installed Go Redis-capacity summary succeeds. At **12:12 UTC**, independent
readback verifies the host sampler's normal scheduled refresh: snapshot time
12:09:57, fresh cache, published availability one and successful completed unit.
The sampler correctly retains its separately deployed source
`cad060906a8fc11b4036ee69ac0a81e26d2a26fb` and its qualified file hash; runtime
health still attests source `c9140316abcf22e5cc6b17316af35c4290c0adba`. The
observation performed no extra capacity scan. No cache, last-attempt, schedule,
budget or policy reset was made. All times here are UTC.

## Lightpanda and operating limits

Installed **1.1.0-nightly.11423+56980afde**, executable source
`56980afdec3ad177072e7a180cb05da838bc1744`. The official
[nightly assets](https://github.com/lightpanda-io/browser/releases/tag/nightly)
advanced October 31. All three executable assets and both Linux OCI layers were
independently verified. Both Linux physical suites, deployment validation and
required CI passed before [PR #11075](https://github.com/colophon-group/jobseek/pull/11075)
merged. Use actual executable source and asset hashes, not the moving Git tag.

The [supported renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/41162854853)
succeeded. Independent 03:16 readback verifies source
`406d0f94ee919962da2ccb4196450344265c9445`, image
`sha256:c09939c34ba034ee9d68ec9176f19f8ddeea04bfa816ce595409b09386133af7`,
ARM64 executable SHA-256
`97710997faea70bb0f22f9b07ba3a9701b47dbb6a71f679150b040e70c565cf3`
and zero restarts/OOMs. AMD64 executable SHA-256 is
`19f3fee25fb14bb75a1e0c1e0f8ab9cce842950544e4a144fbad3884c94b98a3`.
Recheck the official release before the next renderer change.

Production renderer limits remain one CPU, 1 GiB memory, zero swap, 64 PIDs,
concurrency four, 128 MiB heap and 8 MiB responses, with mTLS, controlled egress
and isolated child UIDs. Crawler limits remain two CPU cores, 1 GiB memory,
GOMEMLIMIT 512 MiB, 60 discovery slots, 15 monitor slots and ten candidate host
domains. Preserve schedules, delays and circuits. **Strict Lightpanda and Go
HTTP; no production Chromium fallback.** No forced probes, manual queue/SQL/env
repairs, counter/deadline resets or raised budgets.

## Acceptance and immediate delivery

| Gate | Current evidence and next action |
| --- | --- |
| Production implementation | Native workers, enrichment, document processing, scheduling, persistence, failure handling, sync, migrations, maintenance, exporter and drain deployed. Native CSV publication and currency refresh verified. |
| Enabled configuration coverage | Existing admission proof covers 8,148 unchanged monitor/detail configurations, 1,992,763 scheduled URLs and 47 detail groups. Three boards were subsequently added, none removed; actual startup validates all 8,151 canonical/cache profiles. Reuse proof only for unchanged contracts. |
| Conservation and authority | At 04:44 after the resource window, all 8,151 enabled monitors remain represented: 8,137 queued and 14 valid leases. Zero missing, duplicate, deadletter-only, expired-only, token-presence or cache gaps. Canonical identity hashes unchanged. |
| Canonical output, policy, storage and export | Real PostgreSQL/Redis contracts cover insert/touch/relist/foreign identity, nullable retention, delegation, publisher reservations, rollback and fences. Fresh 1136 readback at 10:28 covers 629 recent completed zero-failure details across 25 provider groups. All 626 nonnull posting hashes match their uploaded SQL description rows; all 630 nonempty locale rows are marked uploaded, none pending. Ten mature Typesense records match canonical title/company. No failed drain event occurs in this bounded window. Both canonical fleet identity hashes match the qualified cold-reversal baseline. Nullable fields and samples do not prove universal completeness. |
| Cold reversal | **Qualified for 1133.** The [supported complete exercise](https://github.com/colophon-group/jobseek/actions/runs/41167908024) passed, with candidate-ready and restored-prior-image markers, no rollback error and promotion skipped. Independent 04:31 reads verify the exact prior source/image/data, all four Go services, unchanged epoch 404 and all 8,151 monitors (8,120 queued, 31 valid leases), with zero missing/duplicate/token/cache gaps. After restoration, 221 completed details span 15 provider groups; all 220 nonnull hashes match nonempty uploaded descriptions, none pending, and ten search records match title/company. The runtime-aware Umantis correction preserves all seven schedules. |
| Equivalent-work efficiency | **Corrected generated comparison qualified; captured/mixed scope open.** Runtime-only [PR #11081](https://github.com/colophon-group/jobseek/pull/11081), exact source `ef7eb52affa698937aff046c4775382953249cc3`, completes all six 536-posting arms with one canonical output hash and verified cleanup. Controls explicitly select Python JSON-LD/DOM extraction and enrichment; Go reaper/exporter/drain remain common measured actors. Mean measured-actor lifetime CPU is 21.29 seconds native / 41.23 Python, sampled peak memory 454.4 / 915.3 MiB, retained memory 436.7 / 867.0 MiB and completion 19.96 / 34.77 seconds. Modeled window allocation is EUR 0.00002392 / 0.00004917 per 1,000 correct postings; startup CPU is separate from that cost window, and modeled allocation is not billed savings. This generated AMD64 scenario does not qualify captured workload or mixed production architecture. The current topology candidate's generated comparison also passes; its first captured arm failed fixture TLS trust before extraction and cleaned up. The local fixture correction is described below. |
| Production freshness and provider recovery | **Open.** At 10:19, 2,386 monitor successes / 2,462 checks in the preceding hour, 4,986 due and zero overdue a day. At 10:28, 2,737 zero-failure details were scraped in the preceding hour, but 1,869,019 details remain due and 1,808,891 overdue a day. The three tracked large monitors retain six/two/five failures and valid recurring representations; they lack new recovery. Both tracked API-sniffer cases retain their 04:34/04:35 successes but now have one failure each and queued retries. Queue and deadletter-marker overlap alone does not attribute the delay, and generic cursor observations do not prove default-owner selection behavior. Observe natural work and do not repair schedules or force attempts. Oracle ADF's normal probe remains 16:23 UTC. |
| Legacy-only retirement | **Open.** Complete the preceding gates and a real schedule/recovery observation window before deleting required compatibility. Preserve useful offline Python and necessary authenticated rollback assets. |

Continue in batches across shared contracts and multiple types:

1. Reuse the qualified 1133 cold reversal for unchanged contracts. Keep all
   8,151 monitor schedules represented through normal work; repeat reversal only
   when the restoration contract changes. Never manually repair schedules.
2. Read the current comparison's numeric diagnostics. Fix the demonstrated cause,
   qualify all four effective profiles in the same iteration, and complete all
   six arms. Do not add speculative infrastructure or repeatedly rerun unchanged
   checks. The candidate includes latest Lightpanda and the 1133 rollback fix;
   its 45 focused fixture tests, lint, format and typing pass. Inspect bounded canonical mismatch counts if convergence still fails.
3. After reversal and warmup, bind one stable whole-lane resource/freshness window
   to the actual installed crawler/renderer identities. Keep errors, skips,
   detail backlog, provider mix and accounting limits visible. Observe natural
   provider recovery and the applicable schedules.
4. When acceptance holds, remove justified production-only Python/Playwright/
   Chromium entrypoints and assets in one coherent batch, retaining isolated
   tooling and authenticated rollback requirements. Verify image contents,
   enabled profiles and operational contracts through required CI, then deploy.

## Current production measurement

The post-reversal window runs **04:35–04:44 UTC**, after five minutes of warmup,
with ten aligned samples per crawler/renderer/PostgreSQL/Typesense host. All
processing generations and pinned identities are stable, with no restart/OOM;
one background crawler container generation changes and is accounted separately.
Over 539.21 seconds, 610 monitors and 983 details succeed, with 357 skipped
details excluded from success counts. Mean complete-lane CPU is **3.59 cores**,
working memory **11.18 GiB**, density **0.823 successful tasks/CPU-second**, and
modeled compute **EUR 0.002151 per 1,000 successes**. Observed output includes 287,165
monitor items, 1,027 detail items and 235,812 posting touches.

Keep the errors visible: 152 failed details, one failed monitor, 197 detail host
circuits, one monitor host circuit, one gone monitor and 11 claim errors. The
post-window bounded current-runtime diagnostic read includes 11 unacknowledged
claims and detail preparation failures. Due monitors fall from 6,455 before the
exercise to 4,936 after the window, while day-overdue monitors fall from 14 to 13;
those are different-time snapshots spanning reversal, not a controlled rate.
Due details remain 1,906,195, including 1,848,274 overdue a day; no zero-backlog
claim is made. Raw samples are deleted after aggregation and readback.

## Efficiency, deployment and retention rules

The 536-posting fixture uses generated inputs. Its earlier complete-lane runs
qualify functionality and scoped mixed-control accounting; they do not alone
establish savings on captured actual workload. Preserve the original requirement
for comparable captured inputs and observed production workload. Account for the actual production x86 crawler and ARM renderer before making
architecture-specific claims; do not
replace that requirement with a synthetic density win.

Natural windows have different task/provider/output mixes and cannot establish
an equivalent-work win. Require better complete-lane CPU/RAM/density/attributable
cost without material throughput/freshness regression. Count useful work with
consistent terminal/output semantics; exclude skipped details from success cost.
Include worker, renderer, reaper, drain, Redis, PostgreSQL and Typesense costs,
startup and retained memory, and disclose fixture/measurement limits. Modeled
allocation is not billed savings; provisioned compute remains EUR 0.0624/hour.
An isolated AMD64 fixture comparison is not ARM64 production performance proof.
Do not weaken the original gate to task counts or component speedups.

Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable
identities, mutation locks, complete writer quiescence, authenticated archived
spec/data restoration and stopped writers on failed restoration. Keep epoch
404 unless the supported protocol changes it. No mutable deploy tags, live
rsync or partial writer restarts. Retain the active generation, journal
references, explicit rollback target, five newest verified v3 generations and
48-hour staging grace. Fresh merge authority must bind the exact head and
actual main tip, with Required CI/Crawler Deploy Gate green and no holds.

Explicit legacy Docker targets and off-profile Compose services currently serve
isolated parity/comparison and compatibility. Workspace tools import some
`src/processing` helpers; blanket Python deletion would remove useful setup,
labelling, research, offline enrichment and operator functionality. Retention
grants no mixed writers or per-task production fallback.

Keep one compact current checkpoint and inputs needed for active work or
supported rollback. **Historical raw evidence need not be preserved.** Delete
consumed captures, profiles, binaries, temporary databases and test/build caches
after aggregation. Approximately 99 GiB is currently free locally. Preserve
pinned worktrees and unrelated changes, including the root checkout's `dump.rdb`.
Update this plan after each deployed batch so resumption requires no historical
private captures.

## Prepared production-only retirement batch

This unreleased **0.13.1138** topology batch is rebased onto merged runtime
source `6934e672a1e10ac5557c025af23f127197af3bbd` (0.13.1137), in
[PR #11080](https://github.com/colophon-group/jobseek/pull/11080). The location
and timeout fixes are already upstream; no Go implementation change remains
in this PR. All 324 operational tests passed on the preceding deployment
implementation; 112 current comparison/admission and affected deployment tests
pass after this rebase. Exact-head CI, numerical acceptance and the changed
restoration cold exercise remain required.

It removes `worker-2`, `worker-3` and `browser-1` from the production spec.
`worker-1` remains an off-profile Go maintenance carrier for authenticated sync.
Forward deployments accept only the native topology. Project-labelled writer
discovery stops older services removed from the incoming spec. Archived legacy
specs retain their bounded pool override; native-only specs do not introduce
missing-image legacy services. Mixed-owner B0 tests use an isolated Compose
fixture. Useful offline Python and required recovery assets remain.

The local test result does not qualify production retirement. Complete output
and resource acceptance and applicable freshness observations first. Publish the
coherent retirement batch through required CI, then exercise the supported full
cold reversal because Compose and restoration contracts change. Keep historical
captures deleted and preserve only the current handoff and recovery assets.

## Native finishing fixes already merged in PR #11081

The shared location path now borrows its read-only miss cache under the existing
executor mutex, removing the map-to-slice-to-map rebuild for every posting.
Borrowed state is restored after each call, including errors; existing owned
cache callers retain their behavior. All 322 frozen Python location cases match.
A local ARM64 indexed-lookup benchmark with 10,000 cached misses reduces
allocation from 1,054,359 to 3,235 bytes per lookup. This isolates allocation
cost; production cache size, timeout attribution and whole-lane savings remain
unproved. PostgreSQL backfill bounds and the one-connection budget are unchanged.

Settled monitor timeouts retain a trusted failing phase for fetch, preparation
or posting writes. They retain the existing deadline-only error identity,
`native_task_timeout_failed` persistence, failure count, deadline, grace, fencing
and recurring schedule. Raw error text and public caller-supplied phase fields
do not enter diagnostics. The new regression fails against the old diagnostic.

Disposable PostgreSQL 17 and Redis checks passed with race detection: three
enrichment tests covering 322 frozen cases, one actual location-load/backfill
contract and twelve worker tests including subtests. No tests skipped. Go vet
passes for the three affected modules. The temporary database and raw test logs
were removed. These are local implementation checks; assign the current next
VERSION and obtain exact-head required CI before publishing this unreleased
batch. Current comparison and production acceptance gates still apply.

The batch now adds a second six-arm comparison using 72 distinct saved HTTP
detail pages: 62 JSON-LD and ten DOM cases, seven source hosts and three recorded
configurations. The workspace capture code saves full decoded HTTP responses,
separately from extracted result JSON. Each accepted response has a matching
successful GET record and a configuration bound by run time; its current Python
parse reproduces every saved extraction field. The corpus retains exact
uncompressed SHA-256 identities in 1.8 MB of compressed fixtures. Qualification
counts include 67 output mismatches, 26 duplicates, 40 unmatched configuration
records, 20 missing board records, 20 incomplete browser captures and eleven
unsupported scraper cases; these are not silently counted as qualified work.

This input measures scheduled direct details through SQL persistence, description
upload and Typesense export, with all actor resources and startup still counted.
It does not capture discovery, browser dependencies, full original response
headers, original network timings, live provider mix or the mixed production
x86/ARM topology. The independent 536-posting generated scenario retains its
monitor scheduling and browser contracts. All 78 native comparison-fixture tests and 23 ARM admission-fixture tests
(101 total), including exact-byte and extraction replay of the captured corpus,
pass locally.
The workflow runs both modes on the final candidate; numerical acceptance is
still pending. Current captured-input qualification is useful evidence, not a
completed whole-lane result or permission to retire required compatibility.

The offline comparator now explicitly disables Go JSON-LD/DOM detail bridges
and selects Python enrichment. Three selector tests exercise the actual runtime
selection functions for both ordinary controls and the ARM B0 control. Earlier
generated results inherited Go extraction defaults; they remain functional
parity evidence and are superseded for Python-baseline resource acceptance. The
new final-head generated and captured results must be inspected before release.

## Immediate release sequence and captured transport correction

Runtime-only [PR #11081](https://github.com/colophon-group/jobseek/pull/11081)
merged as `6934e672a1e10ac5557c025af23f127197af3bbd` after Required CI, deployment
gate, corrected generated comparison, physical ARM B0 admission and fresh merge
authority passed. Its supported complete rollout is running; installed-state
qualification is still pending. This topology candidate is rebased onto that
merged source with version 0.13.1138. Its Go implementation is already upstream;
this PR changes retirement, restoration and isolated acceptance fixtures.
The full migration, captured/mixed resource scope, applicable natural provider
recovery and changed-restoration cold exercise remain required. At 11:33 the
tracked providers still lack new successful recovery; normal retries remain
represented and untouched.

The native HTTP client uses embedded pinned CA roots. The first captured arm
therefore could not trust the isolated fixture's self-signed root even though
its system CA file was mounted. The corrected captured scenario uses the same
explicit canonical `skip_ssl` fixture exception as generated direct requests
in both arms. Page bytes and recorded extraction configurations remain intact;
the workload hash and limits record the transport override. This does not
measure production certificate-verification cost. Request-progress counts now
match each captured path instead of assuming `/jobs/`. All 78 fixture tests
and three focused progress checks pass. Independently built offline Go parsers
match every saved extraction field on all 72 pages; temporary binaries/cache
were removed. Whole-lane captured acceptance remains unqualified.
