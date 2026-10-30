# Go and Lightpanda migration delivery plan

## Current checkpoint — October 30, 2026

Production processing runs **0.13.1126**, source
`18d39d627dee9ecc9ef612408e5aab9a0ced372d`, after the complete
[deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/41049635125).
Both crawler image repositories use native image
`sha256:1952fdc66027692b86fd7198b07dd0bd67397705551e59c882b2d5566f986d15`.
Runtime contract:
`854ee1b61a0aa0f93d390bbbcee1dbc61e38e11e77517862bb831a74c7a16102`.
Installed data revision is the same full source revision; data contract:
`f5f8b863efd90c850e5f1c0c782f4f817961d436b0410135265b6c6099e0d6f5`.
There are **8,148 enabled boards**, schema 0041, default Go ownership at epoch
404, and no active migration plans. Four native actors run; seven legacy actors
are stopped. The processing image has no Python, uv, Playwright or Chromium
command. Runtime, data and renderer identities are independently verified.

The full goal remains **active**. Native implementation and deployment are in
place, but the original acceptance requirement of better whole-lane efficiency
without material throughput/freshness regression is **not established**. Do not
replace that requirement with admission coverage or lower aggregate CPU use.
This checkpoint continues the original
[plan #10185](https://github.com/colophon-group/jobseek/pull/10185) and
[salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177).
All times below are UTC; counts belong to their bound snapshots.

## Latest Lightpanda and production consumers

Installed **1.1.0-nightly.11326+948d77378**, upstream source
`948d773786380bae36f046639fc61e2ddddd212a`. Official Linux
[nightly assets](https://github.com/lightpanda-io/browser/releases/tag/nightly)
were rechecked October 30 and still match the installed pin; this nightly is
newer than stable 1.0.2. ARM64 executable SHA-256:
`74480d0994b0a4e02f7a4956a71bd2827954dbd6fbc18bd5df0ffb1e7258263f`;
AMD64: `9dec394124212f796724d4d32737ba66fdf0319ae6faaf8a1df8cccb4a70e23f`.

The [renderer deployment](https://github.com/colophon-group/jobseek/actions/runs/41033456615)
uses source `93a9c49bb063d70b0b0eca8d19c84e96b44d75e0` and image
`sha256:be79daebf84690c5b40bacd9e6181432d82cc2818836a79e6c213167b4fedfff`.
Executable, image, generation, controlled egress and mTLS readiness were verified.
Limits remain 1 GiB memory, zero swap, concurrency four, 128 MiB heap and 8 MiB
responses. No Chromium fallback is authorized. The installer validates the exact
controller and at most four renderer children.

Native restoration passed both
[CSV publication](https://github.com/colophon-group/jobseek/actions/runs/41033639245)
and [currency refresh](https://github.com/colophon-group/jobseek/actions/runs/41033642676),
with zero restoration failures. The native image also supplies registry sync,
migrations, maintenance, inventory and document conversion.

## Batched corrections and enabled-fleet proof

[PR #11035](https://github.com/colophon-group/jobseek/pull/11035) fixes two migration
regressions together. Occupied monitor slots previously prevented independent-host
details from using spare discovery capacity whenever any monitor was due. Both
HTTP and browser selectors now pass blocked host heads with a bounded cursor,
retaining same-host monitor priority, the shared eight-monitor fairness streak,
host delays, leases and ownership fences.

Inactive, unscheduled Workday attempts with obsolete URLs also failed transport
admission before no-fetch retirement. Retirement now keeps verified canonical
board/posting and opaque lease binding while avoiding obsolete URL/step admission;
settlement rechecks eligibility. Active or reactivated work still needs its valid
current route. Real PostgreSQL/Redis tests cover Workday and API-sniffer retirement
and reactivation; full final-head integration and required CI passed. Retirement
grants no fetch, content write or failure increment.

The exact deployed-source SDK passes **8,148 monitor configurations**, **8,148
detail configurations**, **1,992,763 actual scheduled detail URLs** and **47 detail
profile groups**, with zero eligible lane mismatches or pending normalizations.
All six non-atomic scheduled snapshot gaps were independently resolved: five
unscheduled and one inactive/disabled.

Complete monitor/detail queues and both inflight lanes contain **3,501,973 distinct
identities**, versus 3,501,860 before deployment. All **543 removals** are explained
by fresh canonical/Redis readback: 494 inactive/disabled, 33 unscheduled and 16
currently queued or inflight. All four apparent monitor gaps are now queued or
inflight. All 2,884 stale wrong-worker memberships are ineligible: 495
inactive/disabled and 2,389 unscheduled. There are zero missing canonical records
or unexplained eligible removals. No deadlines, counters or queues were reset.

The original four obsolete-URL cases remain inactive and unscheduled, queued at
positions 499–856 in the bounded readback. Content, publisher flags and zero
failure counters are unchanged; none has completed natural retirement yet. Do not
claim these four settled. The runtime naturally skipped 412 details during the
resource window without restarting.

## Canonical output and storage effects

Current-runtime completed fences and zero-failure canonical states provide a
sample of **1,000 recent active records**: no missing titles, 998 current description
hashes, and **1,003 stored description rows**, all nonempty and uploaded, with zero
pending/inflight description rows in the sample. Salary, occupation, seniority
and technology fields occur in 130, 280, 178 and 270 records respectively.
Detected languages need not have matching localized title variants.
`last_enriched_at` and `to_be_enriched` track separate offline enrichment rather
than shared Go parsing, salary, location or taxonomy extraction.

Actual export readback finds **10 mature current-runtime canonical records in
Typesense**, with all titles and company identities matching. Health, schema and
the scoped company-facet query return HTTP 200; the active/content query finds
2,691,585 documents. Typesense's seven earlier cumulative restarts predate this
window; its generation stays fixed throughout final sampling.

The bounded drain observation has 1,273 upload events and zero PUT HTTP errors.
Reaper observations contain 70 sweeps, 35 lifecycle events, 14 requeues and no
failures, dead letters or missing configurations. The preceding signing correction
naturally uploaded all 100 previously failing objects without manual PUTs or
retry resets. Canonical hashes and publisher/ownership contracts remain fenced.

## Freshness, capacity and resource acceptance

The final window ran **12:24:15–12:33:51**, with ten aligned samples per node,
fixed identities, zero restart increments and no OOM across crawler, renderer,
PostgreSQL and Typesense. Limits stay at 60 discovery and 15 monitor slots, ten
host domains, Go two cores/1 GiB/GOMEMLIMIT 512 MiB, and the renderer bounds above.
Active claims range from 20 to 24; spare capacity remains an observation to explain,
not permission to increase budgets.

| Observation | Legacy control | Native 1126 |
| --- | ---: | ---: |
| Window duration, seconds | 566.8 | 576.1 |
| Successful monitors / details | 726 / 1,454 | 530 / 1,062 |
| Mean whole-lane CPU cores | 5.43 | 3.87 |
| Mean whole-lane working memory, GiB | 11.97 | 11.37 |
| Successful tasks per CPU second | 0.709 | 0.714 |
| Modeled compute EUR per 1,000 successful tasks | 0.001881 | 0.002505 |

Native successful-task throughput is 71.8% of the retained legacy observation,
CPU use 71.3%, working memory 95.0%, and tasks per CPU second 100.8%. Modeled
cost per successful task is **33.2% higher**. Different observation times, task
mix and shared PostgreSQL/Typesense work prevent a causal same-workload comparison.
The native window emits 254,119 monitor inventory items and 1,091 detail outputs;
these are discovery outputs, not unique committed jobs. Legacy inventory output
is unmeasured. The 412 skipped details are excluded from successful-task cost.
No output-normalized density or cost win is proved. Provisioned catalogue compute
remains **EUR 0.0624/hour**; modeled allocation is not billed savings.

Due monitors decrease from 5,830 to 5,593; monitors successful in the preceding
hour increase from 1,913 to 2,154. Among 31 watched overdue monitors, one is checked
and succeeds between 11:35 and 12:35, and 29 remain over a day late. All retain
expected queue projections; statuses are 18 active, ten suspect and three
quarantined, with none publisher-reserved. A deadline-age snapshot finds most
newly day-overdue monitors within the 24–25-hour band. Preserved work and natural
progress do not establish sufficient freshness.

The detail snapshot contains 1,992,473 eligible scheduled records, 1,943,647 due,
1,899,016 overdue by a day, 1,637,555 overdue by a week and 6,672 never scraped;
2,107 zero-failure records were scraped in the preceding hour. Much of this
backlog predates cutover. Preserve real deadlines; no zero-overdue SLO is invented,
and no reset or forced request can establish freshness.

At 12:55, the bounded low-success provider readback contains 22 quarantined boards
and one active board. Oracle ADF remains quarantined with nine failures, last
success October 28 and its existing October 30 14:05 probe deadline. Its implemented
Go HTTP session route is not fresh production recovery evidence. Amazon is active
with one failure, checked at 12:43 but no success since October 29. Do not claim
all enabled upstreams recovered.

## Requirement audit and next delivery

| Original requirement | Current result / remaining work |
| --- | --- |
| Shared salary, location and CPU stages | Native packages deployed; canonical effects and existing parity suites cover the boundary. |
| Native browser executor and ordinary workers | Go owns fetch/render, enrichment, persistence, failure handling, scheduling and fences; no live Python task fallback. |
| Enabled monitor/detail/browser coverage | Full current-fleet SDK admission passes; upstream failures and quarantines remain visible rather than disabled or replaced by empty results. |
| Canonical, publisher, storage and export effects | Final-source integration and natural SQL/R2/Typesense readbacks pass within stated sample limits. |
| Conservation and cold reversal | Complete frontier reconciles; locked full deployment succeeds; supported Go-to-legacy cold reversal was exercised. |
| Runtime consumers and Python boundary | Native production image/maintenance verified; useful offline tooling and required cold-reversal compatibility remain isolated. |
| Whole-lane efficiency and freshness | **Open:** observations do not establish the original improvement/no-material-regression gate. |
| Final legacy removal | **Open:** preserve required rollback assets until acceptance and a schedule-based rollback observation window are established. |

Next, resolve the measured capacity/freshness gap with a bounded comparison of
equivalent effective routes and successful output under existing budgets. Explain
remaining idle capacity and provider failures using read-only queue/host and
canonical evidence; batch demonstrated fixes across affected contracts. Keep the
original efficiency/freshness gate visible. Repeat only affected checks and
operational outcomes after a change or a specific unresolved question. Do not add
optional proof cycles or rewrite the queue/control plane.

Candidate **0.13.1127** addresses measured shared processing overhead across
currency families and monitor/detail profiles together. Salary extraction avoids
regex scans missing a required currency marker; proxy and TLS selection share one
canonical detail read and one profile copy. Fetch and write admission still
revalidate independently. Frozen oracle/race, differential match, fuzz and real
PostgreSQL/Redis transport checks qualify the candidate; required CI, complete
deployment and a natural whole-lane observation are needed before crediting a
production efficiency or freshness improvement. No budget or pacing is raised.

Preserve Oracle ADF quarantine/probe and normal Amazon pacing. Their next natural
outcomes can distinguish current upstream behavior from a migration regression.
Do not force origin work, clear failures or raise quotas for evidence.

## Recovery, Python boundary and cleanup

`ordinary-go`, `lease-reaper`, `exporter` and `drain` own production processing.
Browser execution uses self-hosted Lightpanda; proven HTTP/API routes stay in Go.
Useful Python setup, labelling, research, tests, offline enrichment and operator
tooling remain outside the production processing image. Legacy Docker targets,
Compose and compatibility code remain for explicit cold reversal and offline
parity. Their retention does not authorize mixed writers or per-task fallback.

The complete [cold reversal](https://github.com/colophon-group/jobseek/actions/runs/40899557014)
passed independent image/spec/data/actor checks without mixed ownership. Do not
rewind epoch 404. Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md):
immutable identities, mutation locks, complete quiescence and authenticated
restoration. Retain the active generation, durable journal references, explicit
rollback target, five newest verified v3 generations and 48-hour staging grace.
A failed restoration keeps writers stopped. No mutable tags, live rsync, partial
writer restarts or manual SQL/Redis/environment repair.

Keep one compact current checkpoint and only inputs required for active
verification or supported rollback. Historical evidence need not be preserved.
Delete consumed captures/frontiers, superseded diagnostics, compiled binaries,
inactive environments and build caches after each batch. Protect pinned worktrees,
other contributors' work and production retention. The sanitized facts and exact
release links above make resumption independent of private local history.
