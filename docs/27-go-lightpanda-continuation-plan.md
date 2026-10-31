# Go and Lightpanda migration delivery plan

## Current checkpoint — October 31, 2026

The goal is **active**: deliver the full production crawler migration to Go and
self-hosted Lightpanda or proven Go HTTP/API routes, preserve every enabled board
and useful output, establish better whole-lane efficiency without material
throughput/freshness regression, then retire reasonable legacy-only consumers.
This continues [plan #10185](https://github.com/colophon-group/jobseek/pull/10185)
and [salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177).
Implementation coverage and a Python-free image alone do not close the goal.

Installed **0.13.1132**:

| Identity | Value |
| --- | --- |
| Source and installed data revision | `1a81f408e43bef8535b548f5f1e664f3e7896305` |
| Native image, both crawler repositories | `sha256:d3850d2906f77a764bc3188e468d2550555ac6636cdc27c9ee0d0467b3375792` |
| Runtime contract | `4a9610e027d80e5ae516d349172ea9c720e820c262e9dd3dae15e2fadcd54278` |
| Data contract | `41db9b8ef808b62589f09b4a4407a56155f45fe06fb5a1d0c172da4a1bd66693` |
| Fleet | 8,151 enabled boards; schema 0041; default Go epoch 404; zero active migration plans |

The [full deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/41159304223)
passed on attempt one. [PR #11074](https://github.com/colophon-group/jobseek/pull/11074)
merged after exact-head Required CI and Crawler Deploy Gate passed and fresh
merge authority was checked. Company-image coverage was attested for that exact source.

The independent 02:32 read verifies native `ordinary-go`, `lease-reaper`,
`exporter` and `drain`, started at 02:31:20, with zero restarts/OOMs and unchanged
budgets. None of the five Compose legacy-profile services runs. The processing
image has no Python, uv, Playwright or Chromium. All times here are UTC.

## Latest Lightpanda and fixed operating limits

Installed **1.1.0-nightly.11326+948d77378**, source
`948d773786380bae36f046639fc61e2ddddd212a`. The official
[nightly assets](https://github.com/lightpanda-io/browser/releases/tag/nightly)
and latest stable were rechecked October 31 at 02:26. Installed Linux binaries
still match the newest nightly; stable 1.0.2 is older. The nightly tag is reused,
so compare asset digests and update times. ARM64 SHA-256:
`74480d0994b0a4e02f7a4956a71bd2827954dbd6fbc18bd5df0ffb1e7258263f`;
AMD64: `9dec394124212f796724d4d32737ba66fdf0319ae6faaf8a1df8cccb4a70e23f`.

The verified [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/41033456615)
uses source `93a9c49bb063d70b0b0eca8d19c84e96b44d75e0` and image
`sha256:be79daebf84690c5b40bacd9e6181432d82cc2818836a79e6c213167b4fedfff`.
Renderer limits remain concurrency four, 128 MiB heap, 8 MiB responses, 1 GiB
memory and zero swap, with mTLS and controlled egress. Crawler limits remain two
Go CPU cores, 1 GiB memory, GOMEMLIMIT 512 MiB, 60 discovery slots, 15 monitor
slots and ten candidate host domains. Preserve normal delays, circuits and
schedules. **No Chromium fallback**, forced origin probes, manual queue/SQL/env
repair, deadline/counter resets or raised budgets are authorized.

## Current proof and open gates

| Requirement | Result and remaining work |
| --- | --- |
| Production implementation | Native workers, shared enrichment, document processing, scheduling, persistence, failure handling, sync, migrations, maintenance, exporter and drain are deployed. Native restoration also passed CSV publication and currency refresh. |
| Enabled configuration coverage | Existing admission contract qualified 8,148 monitor/detail configurations, 1,992,763 scheduled URLs and 47 detail groups. Those existing configs are unchanged, three boards were added and none removed. Actual startup validates all 8,151 canonical/cache profiles. Reuse this proof only for unchanged contracts. |
| Conservation and authority | The October 31 02:33 source-bound read represents all 8,151 enabled monitors: 8,122 queued and 29 valid leases. Zero missing, deadletter-only, duplicate, expired-only, token-presence or cache-domain/lane gaps. The full supported deploy restored representation without manual repairs. This is an initial read, not a completed schedule/recovery window. |
| Canonical/publisher/storage/export | Real PostgreSQL/Redis parity covers insert/touch/relist/foreign identity, nullable retention, delegation, publisher reservation, rollback and fences. Current SQL/R2/Typesense samples below qualify observed effects within their limits. |
| Provider recovery | One former deadletter API-sniffer case naturally succeeded before 1131. Accenture, Beisen and browser API-sniffer still have processing failures; Amazon and Oracle ADF lack fresh success. Admission, requeue and active fences are not recovery. |
| Whole-lane efficiency/freshness | **Open.** Equivalent-work improvement without material throughput/freshness regression is not established. Initial 1132 freshness has 6,368 due monitors, 16 overdue a day and 1,093 successful in the past hour; a comparable steady window is still needed. |
| Cold reversal | Earlier supported full cold reversal passed. Fresh archived-spec reversal of the current all-Go generation remains open. The new conservation gap is closed in the initial read; verify it stays closed and qualify current canonical effects before the supported exercise. Preserve immutable rollback assets and epoch monotonicity. |
| Legacy-only retirement | **Open.** Complete acceptance and a schedule-based observation/recovery window before removing required compatibility. Preserve useful offline Python. |

In predecessor 1130 at 20:45, 730 completed active zero-failure detail records span 27 provider
groups; none lacks a title. All 729 nonnull posting hashes match nonempty uploaded
description rows, with no pending/inflight uploads. Ten mature Typesense records
match title and company. Locale/title array lengths have separate contracts;
their differences alone do not establish corruption. Existing values can be
retained by COALESCE. Drain has no PUT errors in its bounded 2,000-event read.
One reaper sweep failed; later sweeps/lifecycle succeeded without new deadletters
or missing configurations. Do not treat zero restarts as sufficient health.

Post-window 1131 readback covers 1,000 completed active zero-failure detail
records in 27 provider groups, with no missing titles. All 979 nonnull posting
hashes match uploaded description rows; 984 nonempty description rows include
five pending uploads and none inflight. Ten mature Typesense titles/companies
match. The bounded drain has no PUT errors and all 56 reaper sweeps plus 28
lifecycle events succeed. Nullable fields, retained values and pending uploads
are part of the existing contract, not universal field-completeness claims.

A separate post-window cohort covers 2,000 recently seen rich-monitor postings
across API-sniffer, Ashby, Greenhouse and Lever. Every posting has a title and an
uploaded description matching its current posting hash; all 2,001 description
rows are nonempty and uploaded. No posting was separately detail-scraped during
this runtime, and none has a detail scheduled. Multilingual description-row
counts differ from posting counts; per-posting existence checks resolve that
accounting difference. Twenty-three postings were inserted and 34 description
rows updated since startup; other fields can be retained. This is bounded
canonical state evidence, not a controlled comparison to predecessor cohorts.

## Shared writer fix and next delivery batch

1130 has 11 observed `posting_write: deadline` failures. Its writer uses per-row
uncached DescribeExec statements, two round trips each, within a 15-second
transaction and five-connection pool. Passive existing-socket RTTs span
0.36–6.90 ms, median 1.92 ms; RTT alone does not prove the timeout cause.

1131 pipelines the unchanged posting statements, resolves returned identities
and concurrent duplicates, then pipelines description statements in the same
fenced transaction. It preserves the 500-row atomic chunk, 15-second budget,
five connections, uncached execution mode, SQL statements, publisher policy and
three-attempt deadlock-only retry. This addresses a shared persistence boundary
across rich monitor types in one iteration.

A controlled actual-PostgreSQL/Redis regression runs 15 concurrent native monitor
claims of 500 rows each with a 6 ms read-latency envelope. Original: five complete,
ten full rollbacks after 15.02 seconds, 10,218 socket reads. Candidate: all 15
complete in 1.26–5.56 seconds across isolated/contract runs, 735–958 reads.
Readback checks full effects or full rollback. All 313 focused tests/subtests
across 24 roots pass, with race checks and no skipped cases. This proves the
transport regression within the test envelope, not production throughput.

The full rollout, independent installed/actor/conservation checks and initial
natural resource window are complete. The bounded 22:30 current-runtime log
contains no posting-write deadline failures, but still has ten unacknowledged
claims, two heartbeat deadlines, two task deadlines and 142 detail failures,
96 classified in detail preparation. This observation is shorter than the
predecessor runtime; it does not establish permanent error elimination.

Candidate [PR #11073](https://github.com/colophon-group/jobseek/pull/11073),
version 0.13.1133, compares 536 identical postings across rich API, direct DOM,
rendered DOM and direct JSON-LD routes. Each arm includes actual workers,
renderer/browser, reaper, PostgreSQL, Redis, exporter, drain and Typesense.
Bootstrap passes real PostgreSQL/Redis canonical/cache admission, production-size
lookups, common feed and guarded exporter ownership transfer. The HTTPS fixture
has verified service identities and a strict certificate chain. No production
ownership or HTTPS guard is weakened.

The latest full Linux run passes those stages and starts the native drain, but
its renderer fails before work begins. The fixture incorrectly overrides the
service entrypoint's root-to-10001 privilege drop and required child-process
capabilities. It now matches the production user, three capabilities and 64-PID
limit. A contract test compares both Compose definitions directly. All 27 local
tests, lint/format, typing, actionlint and Compose parsing pass. Full Linux
qualification remains required. Bounded annotations retain safe engine/startup
codes, not raw logs. Every failed arm has verified cleanup.

The production timeout fix is deployed as 1132 through
[PR #11074](https://github.com/colophon-group/jobseek/pull/11074). Its exact tested
head is `21bee788dd582151d519191c82a9a57bee5970a5`. The real PostgreSQL/Redis
regression reproduces on Lever, Oracle HCM and direct DOM: the old path cancels
before recording failure or rescheduling. The replacement records one failed
cycle and acknowledges its canonical next deadline within the existing
cancellation grace, without further origin work. It preserves incomplete
inventories and SQL/token/epoch/publisher barriers. All 88 focused race
tests/subtests and the complete required Linux CI pass, including cancellation,
stale-token, reservation and receipt recovery. Processing deadlines, lease
budgets, concurrency and retries are unchanged. The deployment restored monitor
representation; natural provider recovery and sustained scheduling remain open.

Next delivery:

1. Diagnose normal scheduling of the five tracked former-deadletter cases with
   bounded read-only queue rank/head, retained authority, host pacing and cache
   evidence. Later reads show new check starts for Accenture and browser
   API-sniffer, but no fresh success; no recovery is credited to 1131. Keep the restored Oracle HCM/Lever/API-sniffer representation under normal
   scheduling observation. Fix shared causes
   rather than force attempts or wait on an unexplained old deadline.
2. Qualify comparable effective routes and useful output for the original
   whole-lane gate. Reuse unchanged worker/enrichment/PDF/drain/executor/policy
   proof; use identical captured inputs for controlled comparisons and normal
   production work for freshness. Repeat broad captures only for changed
   contracts or unexplained gaps. Keep current errors and differing workloads
   visible; do not label natural windows a controlled speedup.
3. Observe actual provider failure/recovery and applicable schedules, then retire
   proven runtime-only consumers after acceptance and the recorded rollback
   window. Oracle ADF remains quarantined until its October 31 16:23 normal probe
   after an October 30 fetch failure; Amazon has no fresh success. Stored errors
   alone do not prove the current cause. No forced origin work, counter resets,
   raised quotas or Chromium substitution are authorized.

## Resource and freshness acceptance

The nearby steady 1130 control ran **21:39:05–21:48:04**, ten aligned samples per
node over 539.46 seconds, unchanged identities/budgets and no restart/OOM changes.
It has 141 successful monitors, 720 successful details and 629 skipped details;
25,096 monitor outputs, 901 detail outputs and 18,914 posting touches. Errors
include 214 failed details, 209 detail host circuits, six monitor timeouts and
11 claim errors. These remain part of the result.

| Observation | Legacy reference | Steady 1130 control | Native 1131 |
| --- | ---: | ---: | ---: |
| Duration, seconds | 566.81 | 539.46 | 539.41 |
| Successful monitors / details | 726 / 1,454 | 141 / 720 | 280 / 810 |
| Mean whole-lane CPU cores | 5.43 | 3.79 | 3.87 |
| Mean whole-lane working memory, GiB | 11.97 | 11.36 | 11.23 |
| Successful tasks per CPU second | 0.709 | 0.421 | 0.522 |
| Modeled compute EUR per 1,000 successes | 0.001881 | 0.004140 | 0.003369 |

1131 ran **22:21:22–22:30:21**, after five minutes of warmup, ten samples per
node. Processing identities are fixed with no restart/OOM; one other crawler
container generation changed and is reported separately. It has 186 skipped
details, 112,377 monitor outputs, 891 detail outputs and 83,995 posting touches.
The window includes 121 failed details, 11 failed monitors, four monitor timeouts,
44 detail/5 monitor host circuits, two gone monitors and nine claim errors.
Compared with the nearby 1130 control, successful-task throughput rises **26.6%**,
density rises **23.8%**, and modeled cost per success falls **18.6%**. CPU rises
2.3%, memory falls 1.1%, detail throughput rises 12.5%, and detail outputs fall
1.1%. These are observations with different workloads, not a causal claim.

Natural task/provider/output mixes differ. Legacy retained logs yield 215,734
streamed monitor outputs but 722 terminal monitor events versus 726 counter
successes; detail outputs cannot be recovered. Native outputs are counted at
settlement. These limits prevent an equivalent-work legacy win claim. Do not
redefine the original gate around task counts or component speedups. Provisioned
compute remains EUR 0.0624/hour; modeled allocation is not billed savings.
The model covers crawler actors/Redis/Alloy plus complete renderer, PostgreSQL
and Typesense nodes, using CPU, memory excluding inactive file pages and
role-specific catalogue prices. Skipped details are excluded from success cost.

Between 20:32 and 21:49, due monitors fall from 6,315 to 5,337 and day-overdue
monitors from **2,697 to 18**. Greenhouse/Ashby/JOIN ready-marker/head lag remains
zero, with original 0.5/0.5/2-second delays. JOIN has a normal open host circuit.
1130 restored canonical deadline ordering and bounded scans past throttled hosts;
first-time/detail fairness and host/lease budgets remain intact. Detail backlog
must also be measured; an earlier snapshot had substantial pre-cutover overdue
work. Preserve actual deadlines rather than inventing a zero-overdue SLO.

Within the 1131 installed reads at 22:17 and 22:30, due monitors rise from
5,891 to 6,018 while day-overdue monitors fall from 16 to 13; preceding-hour
successes fall from 1,586 to 1,435 as the rolling window changes. Eligible
scheduled details fall from 1,996,788 to 1,995,931, due details from 1,915,569 to
1,915,382 and day-overdue details from 1,863,629 to 1,861,790. Zero-failure detail
scrapes in the preceding hour rise from 3,211 to 3,680, while never-scraped
records rise from 6,863 to 7,012. Do not turn mixed snapshots or lower long-overdue
counts into a complete freshness/no-regression proof.

## Recovery, Python boundary and cleanup

The earlier [cold reversal](https://github.com/colophon-group/jobseek/actions/runs/40899557014)
passed independent image/spec/data/actor checks without mixed ownership. It
predates the final all-Go generation; do not credit it as fresh proof of that
generation's archived-spec reversal. No new exercise has been dispatched. Do not
rewind epoch 404. Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md):
immutable identities, mutation locks, full quiescence, authenticated restoration,
and stopped writers on failed restoration. No mutable tags, live rsync or partial
writer restarts. Retain the active generation, journal references, explicit
rollback target, five newest verified v3 generations and 48-hour staging grace.

Untargeted image builds now inherit the native production stage. Installed-image
CI verifies native commands and absence of Python/Playwright/Chromium. Explicit
Python `slim`, `full` and `benchmark-browser` Docker targets, the five off-profile
Compose services and frozen compatibility assets still serve offline parity,
comparison and cold reversal. Qualify authenticated archived-spec restoration
before removing current legacy entrypoints. Useful setup, labelling, research,
offline enrichment and operator tools stay isolated; workspace code still imports
`src/processing` helpers, so blanket Python-source deletion would remove useful
functionality. Retention grants no mixed writers or per-task fallback.

Keep one compact current checkpoint and inputs required for active work or
supported rollback. Historical raw evidence need not be preserved. Consumed
captures, profiles, binaries, temporary databases and build/test caches are
removed after aggregation. Current cleanup recovered about 672 MiB locally;
98 GiB remains free. Read-only service checks found about 36/12/63/23 GiB free
on crawler/renderer/PostgreSQL/Typesense respectively. Their temporary output
and probe were consumed. Preserve pinned worktrees and unrelated contributor
changes. Update this document after each deployed batch so resumption needs no
private historical captures.
