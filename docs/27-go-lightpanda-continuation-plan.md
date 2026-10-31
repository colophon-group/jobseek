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
| Conservation and authority | At 04:12 all 8,151 enabled monitors represented: 8,130 queued and 21 valid leases. Zero missing, duplicate, deadletter-only, expired-only, token-presence or cache gaps. Canonical identity hashes unchanged. |
| Canonical output, policy, storage and export | Real PostgreSQL/Redis parity covers insert/touch/relist/foreign identity, nullable retention, delegation, publisher reservations, rollback and fences. At 04:16, 515 completed zero-failure details span 19 provider groups with no missing titles; 486 current hashes match uploaded descriptions, one upload pending. Ten mature search records match title/company. No failed drain/reaper events in the bounded current-runtime read; reaper recovery includes one deadletter. Nullable/retained fields and samples do not prove universal completeness. |
| Cold reversal | **Open.** The prior all-Go exercise restored exact identities but lost seven Umantis schedules through unconditional parking. 1133 selects parking from the restored runtime backend and normal rollout restores all seven. [Current full exercise](https://github.com/colophon-group/jobseek/actions/runs/41167908024), dispatched 04:16, must finish and independently restore this exact generation, all four Go services, canonical effects and all 8,151 schedules. Promotion must be skipped. Workflow success alone is insufficient. |
| Equivalent-work efficiency | **Open.** [PR #11073](https://github.com/colophon-group/jobseek/pull/11073), version 0.13.1134, runs six alternating complete-lane arms over 536 identical rich API/direct DOM/rendered DOM/direct JSON-LD postings. Require all canonical SQL, uploaded-body and search effects, equal canonical hashes, bounded cgroup CPU/RAM accounting and verified cleanup. The latest completed Linux run starts every actor and uploads all 524 rich API/direct-route postings, with two successful monitors, but produces none of the 12 rendered-route postings. Cleanup passes; efficiency remains unqualified. The candidate now parses the worker's actual bounded text errors and retains canonical per-route monitor outcomes and upstream request counts, with no relaxed deadline/capacity/completeness. |
| Production freshness and provider recovery | **Open.** Last installed read: 6,455 due monitors, 14 overdue a day, 974 successes/1,028 checks in the preceding hour. Detail backlog remains large. Five tracked former-deadletter cases have queued representation but no fresh success; active/completed fences are insufficient. Oracle ADF has its normal October 31 16:23 probe. Observe actual schedule/recovery windows; investigate shared causes using bounded read-only evidence, never force attempts. |
| Legacy-only retirement | **Open.** Complete the preceding gates and a real schedule/recovery observation window before deleting required compatibility. Preserve useful offline Python and necessary authenticated rollback assets. |

Continue in batches across shared contracts and multiple types:

1. Finish the current supported cold exercise and independent restoration checks.
   Do not manually restore missing schedules or credit green workflow status as
   conservation. Fix any demonstrated shared failure through a PR and full
   immutable deployment.
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

## Efficiency, deployment and retention rules

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
