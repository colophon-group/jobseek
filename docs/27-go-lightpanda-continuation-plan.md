# Go and Lightpanda migration delivery plan

## Current checkpoint — October 30, 2026

The last fully verified production checkpoint is **0.13.1128**, source
`d4a41626f6a31f17b8b8227a01ea40467565cbea`, after the complete
[deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/41085351259).
Both crawler image repositories use native image
`sha256:567edc79a60c1b7b7aa1c3c758e733ee6f195f1ab8318b2046ad67d12d164e38`.
Runtime contract:
`03e7ceb9e32ee9191e30c70c4d5de3719289b72e8f4573e4ed5bbc7cdac34e8e`.
Installed data revision is the same full source revision; data contract:
`41db9b8ef808b62589f09b4a4407a56155f45fe06fb5a1d0c172da4a1bd66693`.
There are **8,151 enabled boards**, schema 0041, default Go ownership at epoch
404, and no active migration plans. Four native actors started at 16:11:00;
all remain running without restarts or OOM in the observation below. None of the
five current Compose legacy-profile services runs. The processing image contains
no Python, uv, Playwright or Chromium command. Runtime, data and renderer
identities are independently verified.

[PR #11068](https://github.com/colophon-group/jobseek/pull/11068) merges the
Python-free default image build as 0.13.1129 at
`6c16fa75151727482161bb7d65de577538872019`. Required CI and actual default-image
parity pass. Its [deployment](https://github.com/colophon-group/jobseek/actions/runs/41103411774)
build succeeds, but the company-OG observer fails twice with GitHub HTTP 502
while reading child jobs; both exact dispatched prewarm runs actually succeed.
Host deployment and promotion are skipped. A fresh 18:25 installed read confirms
1128 still serves all 8,151 boards with no native restarts, OOM or legacy workers.
The 1130 candidate polls the existing prewarm run's status, retrying observation
errors without dispatching another child. A completed failed child remains a
failure. The full replacement rollout restores from the authenticated active
v3 generation; a skipped intermediate rollout does not become rollback evidence.

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

[PR #11047](https://github.com/colophon-group/jobseek/pull/11047) batches the next
shared CPU costs across monitor/detail profiles. An immutable technology keyword
prefix index preserves the old substring admission and literal matching;
structured/bare salary scans require their existing currency/period markers;
experience prefix scans are lazy and impossible reversed-wording scans are
filtered. Frozen Python outputs, independent old-filter comparisons, salary
match spans/captures, full enrichment/executor race suites, real PostgreSQL
executor checks and **162,709 differential fuzz executions** passed. Required CI
and the deploy gate passed at exact head before the full immutable rollout.

A private replay of **127 recent stored records across 12 providers** identified
these stages as 82.6% of sampled preparation CPU. Serial two-core runs fall from
4.83–5.37 milliseconds to 2.76–2.78 per record: a conservative observed **42.6%**
reduction, with identical complete prepared-output hashes. The comparison uses
fixed lookup maps and excludes actual location/queue/SQL work. It is component
evidence; whole-lane acceptance uses the production observations below. Consumed
replay content, profiles, binaries, temporary database and caches were removed.

The 1126 SDK passed **8,148 monitor configurations**, **8,148 detail configurations**,
**1,992,763 actual scheduled detail URLs** and **47 detail groups**. All six
non-atomic scheduled snapshot gaps resolved: five unscheduled and one inactive.
The entire admission/fencing/scheduling Go module remains byte-identical in 1128
(Git tree `556c836c182bfc5b6a5bd2699054edd294939bca`); the existing 8,148 board configurations are unchanged. The new data tree adds
three boards and removes none. Atomic canonical company/board-key hashes match
all 8,151 committed registry keys before and after rollout. Actual 1128 startup
validates every enabled canonical/cache profile. This qualifies the unchanged
existing admission contract and the new configurations; no fresh two-million-URL
capture is claimed.

The previous 1127 complete monitor/detail queues and both inflight lanes contained **3,499,609 distinct
identities** after its rollout, versus 3,499,394 before it. All **1,237 removals** resolve
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

At 16:27, current-runtime completed detail fences and zero-failure active
canonical states provide **1,000 recent records across 26 provider groups**:
no missing titles, 994 nonnull description hashes and 1,000 nonempty description
rows, with 994 uploaded and six pending. All 994 uploaded posting hashes match.
Salary, occupation, seniority and technology fields occur in 178, 183, 166 and
285 records respectively. Good existing fields may be retained by COALESCE;
these counts do not prove every field was newly computed by this attempt.
Detected language and localized title variants may differ; offline enrichment
has separate `last_enriched_at` and `to_be_enriched` state.

A 16:37 maturity check covers **2,981 current-runtime completed active records**:
no pending or inflight description uploads, including none older than 30/120
seconds respectively. Its separate 30-second-mature sample has 1,000 records,
996 posting hashes, all 996 matching uploaded rows, and 997 nonempty uploaded
description rows. Hashes/descriptions are nullable for some processing contracts;
do not turn these samples into universal description or hash-integrity claims.

An earlier bounded read found three legacy hash inconsistencies: stored rows
from April 7–8 and nonempty HTTP-200 objects whose modification times also
predate 1127. Their bodies matched neither retained hash. They are classified
existing data limitations, without a new 1128 corruption or repair claim.
Object keys use `job/<posting>/<locale>/latest.html`, independent of hash hints.

Actual Typesense readback finds **10 mature current-runtime records**, with
every title and company identity matching. Health, schema and the scoped company
facet query return HTTP 200. At 16:27 the bounded drain observation contains
1,339 upload events and zero PUT HTTP errors in its last 2,000 code-owned events.
Reaper observations contain 60 sweeps, 30 lifecycle events, 24 requeues and no
failed events, dead letters or missing configurations. Publisher and ownership
fences remain in place; no outbox rows, counters or queues were manually repaired.

## Freshness, capacity and resource acceptance

The final 1128 window ran **16:16:23–16:25:22**, ten aligned samples per node,
with fixed processing identities, zero restart increments and no OOM across
crawler, renderer, PostgreSQL and Typesense. Actual limits stay at 60 discovery
and 15 monitor slots, ten host domains, Go two cores/1 GiB/GOMEMLIMIT 512 MiB,
and the renderer bounds above. Active claims range from 18 to 44.

| Observation | Legacy control | Fresh 1127 baseline | Native 1128 |
| --- | ---: | ---: | ---: |
| Window duration, seconds | 566.8 | 539.1 | 539.2 |
| Successful monitors / details | 726 / 1,454 | 213 / 979 | 286 / 1,165 |
| Mean whole-lane CPU cores | 5.43 | 3.69 | 4.17 |
| Mean whole-lane working memory, GiB | 11.97 | 11.35 | 11.52 |
| Successful tasks per CPU second | 0.709 | 0.600 | 0.646 |
| Modeled compute EUR per 1,000 successful tasks | 0.001881 | 0.003101 | 0.002528 |

The fresh 1127 baseline ran 15:27:03–15:36:02. Relative to it, 1128 has **21.7%
more successful-task throughput**, **7.7% more successes per CPU second**, and
**18.5% lower modeled cost per success**, with 1.5% more working memory.
Monitor inventory outputs increase from 148,493 to 259,822, about 75%; detail
outputs increase from 1,040 to 1,194. These are emitted discovery outputs, not
unique committed jobs. Skipped details are excluded from successful-task cost.

These are natural windows at different times and task mixes. Shared PostgreSQL
and Typesense work varies; Typesense CPU rises from 0.34 to 0.83 cores while
renderer CPU falls from 0.69 to 0.51. No causal same-workload speedup or legacy
output-normalized win is proved. The original legacy samples did not measure
inventory output. A later read of the stopped containers' retained logs recovers
215,734 streamed monitor outputs in that exact control window. All four Python
actors have logs surrounding the window, without reaching the tail bound, but
their 722 terminal monitor events do not reconcile with 726 counter successes;
the fifth, Go claimant has no retained log events. Detail successes were logged
at debug level and cannot be recovered from these logs. Native output is counted
at task settlement, while the recovered legacy output is counted per batch.
These limits prevent a complete equivalent-work comparison. The different
inventory volumes also mean task-count cost alone cannot identify an enrichment
regression or justify further CPU changes without a measured bottleneck.
Modeled native cost per success is still **34.4% above the legacy observation**;
the original efficiency/no-material-regression gate remains open. Provisioned
catalogue compute stays **EUR 0.0624/hour**; modeled allocation is not billed
savings. Cgroup CPU, working set excluding inactive file pages, and official
role-specific catalogue prices underpin the model. Sampled peaks are not
absolute memory peaks.

Between the bound installed reads at 16:12:59 and 16:26:51, due monitors decrease
from 6,188 to 6,121 and preceding-hour successes rise from 1,589 to 1,648.
Day-overdue monitors change from 2,266 to 2,271 as deadlines age. The 16:27 detail
snapshot has 1,994,073 eligible scheduled records: 1,926,695 due, 1,880,014 over
a day, 1,624,482 over a week and 6,670 never scraped; 6,304 zero-failure details
were scraped in the preceding hour. Much of this backlog predates cutover.
Keep real deadlines and the original freshness gate; do not invent a zero-overdue
SLO or reset scheduling state.

Oracle ADF's normal probe ran at **16:23** and failed in fetch, increasing its
failure count to ten. It remains quarantined, with its existing next probe on
October 31 at 16:23 and last success October 28. Amazon was checked at 16:23,
remains active with one failure, and has no success since October 29; its stored
failure is also fetch-phase. Neither has proven fresh production recovery.
No forced origin work or Chromium fallback is authorized. The earlier bounded
lane census does not support browser starvation as the explanation: 104 browser
successes and no browser monitor more than a day late in that observation.

The final window has nine claim errors versus two in the fresh control. The
new shared CPU changes do not establish a fix for their underlying cause.
Zero restarts alone does not establish sufficient freshness or make errors
harmless; preserve the bounded acknowledgement and write-deadline diagnostic lead.

## Requirement audit and next delivery

| Original requirement | Current result / remaining work |
| --- | --- |
| Shared salary, location and CPU stages | Native packages deployed; canonical effects and parity suites qualify the boundary. |
| Native browser executor and ordinary workers | Go owns fetch/render, enrichment, persistence, failure handling, scheduling and fences. |
| Enabled monitor/detail/browser coverage | Existing admission and configs unchanged; new configs pass startup and all 8,151 registry keys match canonical state. ADF/Amazon recovery remains unproved. |
| Canonical, publisher, storage and export | Real integration and natural SQL/R2/Typesense effects verified within sample limits; bounded hash/upload samples and pre-existing inconsistencies stated above. |
| Conservation and cold reversal | Complete frontier reconciles; full locked deployment and supported cold reversal pass. |
| Runtime consumers and Python boundary | Native production image/maintenance verified; useful offline tooling and required reversal compatibility stay isolated. |
| Whole-lane efficiency and freshness | **Open:** observations do not establish the original improvement/no-material-regression gate. |
| Final legacy removal | **Open:** retain required rollback assets until acceptance and a schedule-based observation window are established. |

Next batch: resolve the remaining measured processing/acknowledgement cost and
known provider recovery gaps under the existing CPU, pool and host limits.
Use existing code-owned diagnostics and canonical evidence to distinguish
transport/session failures from upstream conditions; do not infer recovery from
profile admission. Batch demonstrated shared fixes across affected profiles.
The source audit also shows ordinary legacy rich refresh recomputes shared fields;
do not assume all refresh preparation can be skipped without changing output.

The 17:35–17:45 read-only schedule check instead identifies a shared freshness
bottleneck: Greenhouse, Ashby and JOIN have 1,883, 402 and 114 day-overdue
monitors respectively. Their ready monitor domains have no current rate wait or
open host circuit. Their queue heads retain roughly 30 hours of overdue age,
but ready markers replace 29–30 hours of that age with a previous throttle floor,
placing these backlogs behind hundreds of other domains. Simple-lane first-time
work is empty in the observations, so first-time priority does not explain this
ordering. A private real-Redis regression reproduces the deadline displacement
in both lanes. The next candidate keeps canonical monitor deadline ordering and
uses bounded scans past throttled hosts across claim, reschedule, enqueue,
registry sync, lease recovery and native retirement paths. Frozen B0 compatibility
programs retain their exact generation binding. The candidate retains first-time and
detail fairness, host delays, lease fences, concurrency and the ten-domain scan
budget. Qualification and installed rollout must precede any claim of recovery;
do not repair production queue scores manually.

At 18:58, a focused canonical/Redis check accounts for all 8,151 enabled
monitors: 8,146 are queued or validly leased and five remain in retained
deadletters. There are no unexplained missing monitors, duplicate representations
or cache domain/lane mismatches. The five deadletters span Accenture,
API-sniffer (both lanes), Beisen and Amazon; accounting for them does not prove
recovery. Compare the same monitor identity/key hashes after the candidate's
full rollout without manually replaying deadletters or forcing origin work.

Preserve the original efficiency/freshness requirement. Qualify equivalent
effective routes and useful output, then repeat only affected operational checks.
Reuse unchanged admission/policy/conservation proof instead of another full
frontier or two-million-URL capture without a changed contract or unexplained gap.
Do not raise concurrency, create a control plane or reserve browser capacity on
speculation.

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

Retirement inventory: the Dockerfile still has explicit Python `slim`, `full`
and `benchmark-browser` compatibility targets. Whole-lane comparison and
runtime-contract workflows still consume named legacy targets. Untargeted
crawler image builds inherit the native Go image through the final `production`
stage; installed-image CI builds without `--target` and checks the actual image
for native executables and absence of Python, Playwright and Chromium. Production
deployments continue to select `native` explicitly. Five Compose
services remain behind the explicit `legacy` profile. The deployment rollback
restores the authenticated prior spec archive and exact old image/pool overrides;
qualify that archived path before removing current legacy entrypoints/profiles.
`src/processing` also serves offline workspace code, including
`workspace/lib/run.py`'s `scrape._apply_defaults` import. A blanket Python-source
deletion would remove useful tooling. Retire only proven runtime-only consumers
after the stated acceptance/window, preserving isolated tooling and recovery.

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
