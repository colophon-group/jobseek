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

Installed **0.13.1135**, independently verified October 31 at 09:13 UTC:

| Identity | Value |
| --- | --- |
| Source and installed data revision | `cad060906a8fc11b4036ee69ac0a81e26d2a26fb` |
| Native image | `sha256:0931acbce9062f4e003b65b1a9f79ac2d766c28632a896308f62517c0d3beabf` |
| Runtime contract | `46a593686eeba048cfce752698d35de5b3162a1ac24097a4b40277e8ad06f314` |
| Data contract | `41db9b8ef808b62589f09b4a4407a56155f45fe06fb5a1d0c172da4a1bd66693` |
| Fleet | 8,151 enabled boards; schema 0041; default Go epoch 404; zero active migration plans |

[PR #11078](https://github.com/colophon-group/jobseek/pull/11078) merged after
exact-head required checks and fresh authority. The [supported full rollout and promotion](https://github.com/colophon-group/jobseek/actions/runs/41186187072)
and [host telemetry rollout](https://github.com/colophon-group/jobseek/actions/runs/41186187104)
succeeded. Independent reads verify all four Go actors on the exact image with
zero restarts/OOMs and unchanged budgets. No legacy processor runs; the
processing image exposes no Python, uv, Playwright or Chromium commands.

All 8,151 monitors remain represented: 8,127 queued and 24 valid leases, with
zero missing, duplicate, expired-only, token-presence or cache gaps. At 09:13,
2,150 monitors succeeded and 2,201 were checked in the preceding hour; 5,236 are
due and none is overdue a day. These are bounded observations, not universal
freshness or detail-backlog claims.

The installed Go Redis-capacity summary now succeeds. The sampler file hash,
six native execution series and fresh host textfile match. Its cached snapshot
remains stale until the unchanged six-hour retry permits refresh at **12:09:18
UTC**. No cache, last-attempt, schedule, budget or policy resets were made. All
times here are UTC.

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
| Canonical output, policy, storage and export | Real PostgreSQL/Redis parity covers insert/touch/relist/foreign identity, nullable retention, delegation, publisher reservations, rollback and fences. At 04:44, 1,000 completed zero-failure details span 31 provider groups with no missing titles; all 967 nonnull posting hashes match uploaded descriptions. All 968 nonempty description rows are uploaded, none pending. Ten mature search records match title/company. No failed drain/reaper events in the bounded current-runtime read; reaper recovers 16 leases without new deadletters. Nullable/retained fields and samples do not prove universal completeness. |
| Cold reversal | **Qualified for 1133.** The [supported complete exercise](https://github.com/colophon-group/jobseek/actions/runs/41167908024) passed, with candidate-ready and restored-prior-image markers, no rollback error and promotion skipped. Independent 04:31 reads verify the exact prior source/image/data, all four Go services, unchanged epoch 404 and all 8,151 monitors (8,120 queued, 31 valid leases), with zero missing/duplicate/token/cache gaps. After restoration, 221 completed details span 15 provider groups; all 220 nonnull hashes match nonempty uploaded descriptions, none pending, and ten search records match title/company. The runtime-aware Umantis correction preserves all seven schedules. |
| Equivalent-work efficiency | **Open.** [PR #11073](https://github.com/colophon-group/jobseek/pull/11073), version 0.13.1136, runs six alternating complete-lane arms over 536 identical rich API/direct DOM/rendered DOM/direct JSON-LD postings. The latest Linux native arm completes all 536 canonical SQL, uploaded-body and search effects in 20.27 seconds, with cleanup verified. The legacy arm passes content/search checks, then fails the future monitor gate. Source and a failing regression show that the verifier wrongly requires Python's later Redis reschedule clock to equal its SQL success clock. The fix retains the native 1 ms equality check; legacy SQL must retain the full configured day after success, with Redis bounded between SQL due and the completed observation plus that day. Missing, past, early, over-delayed and changed-interval schedules still fail. All 62 focused fixture tests pass. Six-arm parity remains unqualified. Generated all-AMD64 inputs do not establish actual-input efficiency for the production x86 crawler plus ARM renderer. |
| Production freshness and provider recovery | **Open.** At 08:21, 1,551 checks/1,356 successes in the preceding hour, 5,909 due monitors and zero overdue a day. Both lanes make progress. The two tracked API-sniffer cases retain natural successes and zero failures. Accenture, Amazon and Beisen now have natural attempts and partial SQL progress (2,000/5,678/3,500 touched rows), but still reach the unchanged ten-minute deadline. An 18-row offline replay matches Python/Go text-stage outputs; it does not identify the production bottleneck. Five read-only PostgreSQL activity snapshots show no long wait; they do not exclude a bottleneck. Do not optimize from an unproved cause. Oracle ADF's normal probe is October 31 16:23 UTC. Preserve schedules and observe recovery. |
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

The current 536-posting fixture uses generated inputs. Its complete-lane runs
qualify functionality and controlled scenario accounting; they do not alone
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
