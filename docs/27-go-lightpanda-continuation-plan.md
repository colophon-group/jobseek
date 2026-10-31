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

Installed **0.13.1133**, independently verified October 31 at 04:12 UTC:

| Identity | Value |
| --- | --- |
| Source and installed data revision | `d7e696b9fbd0ca74e7ad14f1c6d05a6e3a95b92d` |
| Native image | `sha256:f2a0fea4a2b6639bbfab4cd823b94ed1013cdd5a16a6627b0fa07e908c489539` |
| Runtime contract | `a3fd17f836ff7056c2b10567c95b0c736c51b4e73db689ad1985464efdd82e70` |
| Data contract | `41db9b8ef808b62589f09b4a4407a56155f45fe06fb5a1d0c172da4a1bd66693` |
| Fleet | 8,151 enabled boards; schema 0041; default Go epoch 404; zero active migration plans |

[PR #11076](https://github.com/colophon-group/jobseek/pull/11076) merged after
exact-head Required CI and Crawler Deploy Gate passed and fresh merge authority
was checked. The [full deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/41166591674)
succeeded. Four native services (`ordinary-go`, `lease-reaper`, `exporter`,
`drain`) started at 04:09:59 with zero restarts/OOMs and unchanged budgets. None
of the five legacy-profile services runs. The production processing image has
no Python, uv, Playwright or Chromium. All times here are UTC.

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
| Equivalent-work efficiency | **Open.** [PR #11073](https://github.com/colophon-group/jobseek/pull/11073), version 0.13.1134, runs six alternating complete-lane arms over 536 identical rich API/direct DOM/rendered DOM/direct JSON-LD postings. Require all canonical SQL, uploaded-body and search effects, equal canonical hashes, bounded cgroup CPU/RAM accounting and verified cleanup. The latest completed Linux run starts every actor and uploads all 524 rich API/direct-route postings, with two successful monitors, but produces none of the 12 rendered-route postings. Cleanup passes; efficiency remains unqualified. The next run confirms the rendered monitor is claimed and fails before any upstream request. An actual generated-PKI native-client/renderer TLS1.3 and canonical-hello exchange passes locally. The fixture public CA bundle was mode `0600` owned by parent UID 10001 and unreadable to browser UID 10002; the candidate makes only this public bundle `0444`, retaining private-key isolation and a `0700` host directory. A regression fails before the correction and passes afterward. Full Linux rerun must qualify the rendering path; no deadline/capacity/completeness is relaxed. |
| Production freshness and provider recovery | **Open.** At 04:44: 4,936 due monitors, 13 overdue a day, 2,361 successes/2,559 checks in the preceding hour. The two tracked API-sniffer cases (HTTP and browser) naturally succeed after restoration at 04:35/04:34 with failure counters zero. Accenture is quarantined after five failures; Amazon and Beisen still lack fresh success. Detail backlog remains large; active/completed fences alone are insufficient. Oracle ADF has its normal October 31 16:23 probe. Observe actual schedule/recovery windows; investigate shared causes using bounded read-only evidence, never force attempts. |
| Legacy-only retirement | **Open.** Complete the preceding gates and a real schedule/recovery observation window before deleting required compatibility. Preserve useful offline Python and necessary authenticated rollback assets. |

Continue in batches across shared contracts and multiple types:

1. Reuse the qualified 1133 cold reversal for unchanged contracts. Keep all
   8,151 monitor schedules represented through normal work; repeat reversal only
   when the restoration contract changes. Never manually repair schedules.
2. Read the current comparison's numeric diagnostics. Fix the demonstrated cause,
   qualify all four effective profiles in the same iteration, and complete all
   six arms. Do not add speculative infrastructure or repeatedly rerun unchanged
   checks. The candidate includes latest Lightpanda and the 1133 rollback fix;
   its 31 focused local tests, lint and format pass; qualify the next full Linux run.
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
for comparable captured inputs and observed production workload. Match the
production ARM64 architecture before making architecture-specific claims; do not
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
