# Go and Lightpanda migration delivery plan

## Current checkpoint — October 30, 2026

Production processing runs **0.13.1127**, source
`09658eeee4f4bdc823a6dcc61202f22c5ebd6cdd`, after the complete
[deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/41067006705).
Both crawler image repositories use native image
`sha256:6abc122eca99368ce5d07ab8fb0a9f958e173ad03b5e974c5e581fd5c1229dc4`.
Runtime contract:
`b40bdfadf8816e4fda10be60aedae87536e9db66002a6caa4891434262e0423c`.
Installed data revision is the same full source revision; data contract:
`f5f8b863efd90c850e5f1c0c782f4f817961d436b0410135265b6c6099e0d6f5`.
There are **8,148 enabled boards**, schema 0041, default Go ownership at epoch
404, and no active migration plans. Four native actors started at 14:07:51;
all remain running without restarts or OOM in the observation below. None of the
five current Compose legacy-profile services runs. The processing image contains
no Python, uv, Playwright or Chromium command. Runtime, data and renderer
identities are independently verified.

The full goal remains **active**. Native implementation and deployment are in
place, but the original acceptance requirement of better whole-lane efficiency
without material throughput/freshness regression is **not established**. Admission
coverage and a faster component do not establish that result. This checkpoint
continues the original [plan #10185](https://github.com/colophon-group/jobseek/pull/10185)
and [salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177).
All times below are UTC; counts belong to their bound snapshots.

## Latest Lightpanda and production consumers

Installed **1.1.0-nightly.11326+948d77378**, upstream source
`948d773786380bae36f046639fc61e2ddddd212a`. Official Linux
[nightly assets](https://github.com/lightpanda-io/browser/releases/tag/nightly)
were rechecked October 30 and still match the installed pin; this nightly is
newer than stable 1.0.2. ARM64 executable SHA-256:
`74480d0994b0a4e02f7a4956a71bd2827954dbd6fbc18bd5df0ffb1e7258263f`;
AMD64: `9dec394124212f796724d4d32737ba66fdf0319ae6faaf8a1df8cccb4a70e23f`.
The nightly tag is reused; asset update times identify the current binaries.

The [renderer deployment](https://github.com/colophon-group/jobseek/actions/runs/41033456615)
uses source `93a9c49bb063d70b0b0eca8d19c84e96b44d75e0` and image
`sha256:be79daebf84690c5b40bacd9e6181432d82cc2818836a79e6c213167b4fedfff`.
Executable, image, generation, controlled egress and mTLS readiness were verified.
Limits remain 1 GiB memory, zero swap, concurrency four, 128 MiB heap and 8 MiB
responses. Only Lightpanda and proven Go HTTP/API routes are authorized. The
installer validates the exact controller and at most four renderer children.

Native restoration passed both
[CSV publication](https://github.com/colophon-group/jobseek/actions/runs/41033639245)
and [currency refresh](https://github.com/colophon-group/jobseek/actions/runs/41033642676),
with zero restoration failures. The native image also supplies registry sync,
migrations, maintenance, inventory and document conversion.

## Batched delivery and enabled-fleet proof

[PR #11035](https://github.com/colophon-group/jobseek/pull/11035) restored spare
independent-host detail capacity while monitor slots are occupied, for both HTTP
and browser lanes. Bounded cursors retain same-host monitor priority, the shared
eight-monitor fairness streak, host delays, leases and ownership fences. It also
allows verified inactive, unscheduled obsolete-URL attempts to retire without
fetching. Real PostgreSQL/Redis tests cover retirement and reactivation for
Workday and API-sniffer variants. Active work still requires its current route.

[PR #11038](https://github.com/colophon-group/jobseek/pull/11038) reduces shared
processing overhead across currency families and monitor/detail profiles together.
Salary extraction skips scans whose required currency markers are absent;
regex engine, match spans/captures, context and ordered output remain unchanged.
Proxy and TLS selection now share one canonical detail read and profile copy;
fetch/write admission still revalidates independently. Qualification includes
2,664 salary oracle cases, all guarded match spans/captures, 17,003 fuzz executions,
enrichment/executor race suites, and actual PostgreSQL/Redis transport, publisher,
retirement and executable-selection contracts. Exact-head Required CI and Crawler
Deploy Gate passed before merge and the full immutable rollout.

The 1126 SDK passed **8,148 monitor configurations**, **8,148 detail configurations**,
**1,992,763 actual scheduled detail URLs** and **47 detail groups**. All six
non-atomic scheduled snapshot gaps resolved: five unscheduled and one inactive.
The entire admission/fencing/scheduling Go module is byte-identical in 1127
(Git tree `556c836c182bfc5b6a5bd2699054edd294939bca`); the data contract is unchanged,
and actual 1127 startup validates every enabled canonical/cache profile. This is
an unchanged-contract qualification, not a new two-million-URL capture.

Complete monitor/detail queues and both inflight lanes contain **3,499,609 distinct
identities** after rollout, versus 3,499,394 before it. All **1,237 removals** resolve
through independent canonical/Redis readback: 1,101 inactive/disabled, 131 unscheduled
and five currently queued/inflight. All four apparent monitor gaps are now queued
or inflight. There are zero malformed memberships, missing canonical records,
unexplained eligible removals or unresolved expired retained attempts. Snapshot
reads are non-atomic; no deadline, counter or queue was reset.

The original four obsolete-URL cases remain inactive and unscheduled, queued at
positions 499–856 in their bounded readback, with unchanged content and counters.
Do not claim those four settled or force them to complete. Real variant tests and
186 natural skipped details in the final resource window establish the broader
no-fetch path without inventing a per-record settlement quota.

## Canonical output and storage effects

Current-runtime completed detail fences and zero-failure active canonical states
provide **1,000 recent records**: no missing titles, 1,000 nonnull description
hashes, and **1,000 nonempty uploaded description rows**, with zero pending/inflight
rows in the sample. Of those, 998 current posting hashes match an uploaded row.
Salary, occupation, seniority and technology fields occur in 125, 235, 117 and
192 records respectively. Retained good fields need not have been newly computed
by this attempt. Detected language and localized title variants may differ;
`last_enriched_at` and `to_be_enriched` track separate offline enrichment.

A broader bounded read finds three description hash inconsistencies. All three
stored rows date April 7–8. Read-only signed GETs using the running drain's exact
bucket/endpoint return HTTP 200 and nonempty objects; all object modification
times also predate this runtime. Their bodies match neither retained hash.
This is an existing data consistency limitation, not evidence of a new 1127 write;
do not report universal hash integrity or manually repair it. Object keys use
`job/<posting>/<locale>/latest.html`, independent of the hash/version hint.

Actual Typesense readback finds **10 mature current-runtime canonical records**,
with every title and company identity matching. Health, schema and the scoped
company-facet query return HTTP 200. The bounded drain observation contains
1,661 upload events and zero PUT HTTP errors in its last 2,000 code-owned events.
Reaper observations contain 80 sweeps, 40 lifecycle events, 24 requeues and no
failures, dead letters or missing configurations. Existing publisher and ownership
fences remain in place.

## Freshness, capacity and resource acceptance

The final 1127 window ran **14:19:29–14:28:29**, ten aligned samples per node,
with fixed processing identities, zero restart increments and no OOM across
crawler, renderer, PostgreSQL and Typesense. Actual limits stay at 60 discovery
and 15 monitor slots, ten host domains, Go two cores/1 GiB/GOMEMLIMIT 512 MiB,
and the renderer bounds above. Active claims range from 24 to 57.

| Observation | Legacy control | Fresh 1126 baseline | Native 1127 |
| --- | ---: | ---: | ---: |
| Window duration, seconds | 566.8 | 539.1 | 540.6 |
| Successful monitors / details | 726 / 1,454 | 316 / 803 | 252 / 909 |
| Mean whole-lane CPU cores | 5.43 | 3.97 | 4.19 |
| Mean whole-lane working memory, GiB | 11.97 | 11.24 | 11.38 |
| Successful tasks per CPU second | 0.709 | 0.523 | 0.512 |
| Modeled compute EUR per 1,000 successful tasks | 0.001881 | 0.003279 | 0.002936 |

The fresh 1126 baseline ran 13:47–13:56. Relative to it, 1127 completes more details
and more successful tasks overall, with about 10.4% lower modeled cost per success,
but slightly fewer successes per CPU second. Typesense CPU rises from 0.63 to
1.24 cores. Different task mix, observation times and shared database/search work
prevent a causal same-workload claim. Monitor inventory outputs are similar
(166,789 versus 166,437) despite different monitor counts; they are not unique
committed jobs. Legacy inventory output is unmeasured. The 186 skipped details
are excluded from successful-task cost. Modeled 1127 cost per success is still
about **56% above the legacy observation**. Provisioned catalogue compute remains
**EUR 0.0624/hour**; modeled allocation is not billed savings.

The same frozen rich preparation benchmark, same machine and two Go cores,
falls from 178–385 microseconds/item to 61.6–62.1. Comparing the fastest baseline
with the slowest candidate gives **65.2% lower preparation time**. This component
result does not establish whole-lane efficiency. Cgroup CPU, working set excluding
inactive file pages, and official role-specific catalogue prices underpin the
whole-lane model. One background crawler generation changes during the baseline;
processing actors stay fixed. Sampled peaks are not absolute memory peaks.

Due monitors decrease from 6,182 to 6,029 and preceding-hour successes increase
from 1,567 to 1,721. Day-overdue monitors change from 2,333 to 2,338 as deadlines
age. Eligible scheduled details total 1,991,115: 1,928,729 due, 1,884,918 over a day,
1,627,890 over a week and 6,641 never scraped; 5,041 zero-failure details were
scraped in the preceding hour. Preserve real deadlines and the original freshness
gate; no zero-overdue SLO or reset is introduced.

At 14:50 Oracle ADF remains quarantined with nine failures, last success October
28 and its existing 14:05 probe still awaiting a natural check. Amazon was checked
at 14:47, remains active with one failure, and has no success since October 29.
Their stored native failures are fetch-phase. HTTP session implementation is not
fresh production recovery proof. Browser starvation is not supported by the
preceding bounded lane census: 104 current-runtime browser successes and no
browser monitor more than a day late.

The final window has ten claim acknowledgement errors and two posting-write
deadlines in bounded, code-owned diagnostics, plus 59 failed and 727 circuit-open
detail outcomes. These are concrete remaining diagnostic leads; zero restarts
alone does not make them harmless or establish sufficient freshness.

## Requirement audit and next delivery

| Original requirement | Current result / remaining work |
| --- | --- |
| Shared salary, location and CPU stages | Native packages deployed; canonical effects and parity suites qualify the boundary. |
| Native browser executor and ordinary workers | Go owns fetch/render, enrichment, persistence, failure handling, scheduling and fences. |
| Enabled monitor/detail/browser coverage | Full current-fleet admission qualified through unchanged module/data; upstream failures remain visible. |
| Canonical, publisher, storage and export | Real integration and natural SQL/R2/Typesense effects verified within sample limits; three pre-existing hash inconsistencies classified. |
| Conservation and cold reversal | Complete frontier reconciles; full locked deployment and supported cold reversal pass. |
| Runtime consumers and Python boundary | Native production image/maintenance verified; useful offline tooling and required reversal compatibility stay isolated. |
| Whole-lane efficiency and freshness | **Open:** observations do not establish the original improvement/no-material-regression gate. |
| Final legacy removal | **Open:** retain required rollback assets until acceptance and a schedule-based observation window are established. |

Next batch: distinguish claim acknowledgement latency and posting-write deadline
causes under the existing pool, CPU and host limits, using bounded read-only
queue/database and code-owned error evidence. Batch demonstrated shared fixes
across affected profiles; do not raise concurrency, add a control plane or reserve
browser capacity on speculation. Qualify equivalent effective routes and useful
output, then repeat only affected operational checks. Reuse unchanged admission,
policy and deployment proof instead of collecting another full frontier or SDK
capture without a changed contract or unexplained gap.

Candidate **0.13.1128** batches the next measured shared CPU costs across
monitor/detail profiles: an immutable technology keyword prefix index preserves
the old substring admission and literal matching; structured/bare salary scans
require their existing currency/period markers; experience prefix scanning is
lazy and impossible reversed-wording scans are filtered. A private replay of
127 recent stored records across 12 providers identified these stages as 82.6%
of preparation CPU. Serial same-input runs fall from 4.83–5.37 milliseconds
to 2.76–2.78 per record, at least 42.6% less preparation time; complete prepared
output hashes match for every record. This excludes actual location/queue/SQL
work and uses fixed lookup maps. Frozen Python outputs and old-filter
differential tests qualify semantics; actual PostgreSQL executor checks
qualify the persistence boundary. This component work does not close whole-lane
acceptance. Complete required CI and the full immutable rollout, then measure
natural throughput, freshness and whole-lane cost under unchanged budgets.

Keep normal ADF quarantine/probe and Amazon pacing; their next natural outcomes
can distinguish upstream behavior from migration regression. Do not force origin
work, clear failures or raise quotas for evidence. After performance/freshness
acceptance, observe the applicable normal schedules and failure/recovery cycles
before retiring legacy-only assets. Never redefine the goal to close these gates.

## Recovery, Python boundary and cleanup

`ordinary-go`, `lease-reaper`, `exporter` and `drain` own production processing.
Browser execution uses self-hosted Lightpanda; proven HTTP/API routes stay in Go.
Useful Python setup, labelling, research, tests, offline enrichment and operator
tooling remain outside the production processing image. Legacy Docker targets,
Compose and compatibility code remain for explicit cold reversal and offline
parity; retention grants no mixed writers or per-task fallback.

The complete [cold reversal](https://github.com/colophon-group/jobseek/actions/runs/40899557014)
passed independent image/spec/data/actor checks without mixed ownership. Do not
rewind epoch 404. Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md):
immutable identities, mutation locks, complete quiescence and authenticated
restoration. Retain the active generation, durable journal references, explicit
rollback target, five newest verified v3 generations and 48-hour staging grace.
A failed restoration keeps writers stopped. No mutable tags, live rsync, partial
writer restarts or manual SQL/Redis/environment repair.

Keep one compact current checkpoint and only inputs required for active work or
supported rollback. Historical evidence need not be preserved. Both consumed
full queue captures, profiles/binaries, temporary database fixture and build/test/
fuzz caches have been removed. Delete consumed diagnostic inputs and raw samples
after compact aggregation. Protect pinned worktrees, unrelated contributor work
and production retention. Exact identities and release links above allow resuming
without private historical captures.
