# Go and Lightpanda migration delivery plan

## Current checkpoint — October 30, 2026

Production processing runs **0.13.1125**, source
`35faab042a615edcdaf0a7f038f0a443ff417719`, following the complete
[deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/40984824139).
Both crawler image repositories use native image
`sha256:0685a903244d614bc8b9d12d1a43832753de67d06cb423027299aa609bb56b3c`.
The runtime contract is
`68adb59aebc6391cd6491c6d425d3f94755a404764763f81b51368303811db6d`.
Schema is 0041; Go owns default processing at epoch 404, with no active migration
plans. Four native actors run and seven legacy processors are stopped. The
installed processing image contains no Python, uv, Playwright or Chromium command.

The current catalogue has **8,147 enabled boards**, data revision
`5c7d4524f6e6a58c7ba168a19c82b3bb44cf3adb`, contract
`7806967e5828d6ce702e3320103d7f556272f53aea17029fa9e8577121c9dcdb`.
Runtime source, data revision and renderer source are separate verified identities;
a data-only or deployment-tool change does not require rebuilding crawler code.

The full migration goal remains **active**. Fleet admission, conservation,
stability and persistence pass; freshness/capacity acceptance and the full
completion audit remain open. Work continues
from the merged [salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177)
and [delivery plan #10185](https://github.com/colophon-group/jobseek/pull/10185).
Times below are UTC; fleet counts describe the bound observation.

## Complete enabled-fleet admission and conservation

The exact installed-source SDK passed all **8,147 monitor configurations**,
**8,147 detail configurations** and **2,004,075 actual scheduled detail URLs**.
It checks canonical/cache identity, configured profiles and worker lanes,
fallback steps, intervals and actual URL admission. There were no snapshot queue
gaps, lane mismatches or pending lane normalizations. Detail qualification covers
47 profile groups. This is full-fleet admission evidence, distinct from successful
upstream content retrieval or a freshness SLO.

The complete main queue/inflight boundary contains **3,499,584 identities**,
compared with 3,493,081 before the window. All 1,684 removals are explained by fresh
canonical/Redis observations: **1,431 inactive/disabled**, **249 unscheduled** and
**four currently queued or inflight**. All four apparent monitor-route gaps are
currently queued or inflight. Of 2,926 stale worker-lane memberships, **537** are
inactive/disabled and **2,389** unscheduled. There are no missing canonical records
or unexplained eligible removals. No deadlines, counters or queues were reset.

Retained stale memberships do not authorize execution: default admission checks
canonical eligibility under its existing lock before activating a claim. An
ineligible raw attempt remains available to bounded recovery and cannot replace a
productive fence or write content. Native service exits during the two registry
publications were observed with configuration/authority-loss diagnostics, followed
by cancellation and restart. Conservation is proved independently; resource
acceptance pins the subsequent process generation and cumulative restart counters
rather than pretending those earlier exits did not occur.

## Latest Lightpanda and deployment consumers

Installed **1.1.0-nightly.11326+948d77378**, upstream source
`948d773786380bae36f046639fc61e2ddddd212a`, from the latest verified official
[release assets](https://github.com/lightpanda-io/browser/releases/tag/nightly).
The installed ARM64 executable SHA-256 is
`74480d0994b0a4e02f7a4956a71bd2827954dbd6fbc18bd5df0ffb1e7258263f`.
Both Linux platform assets and the immutable upstream OCI platform contents were
verified; platform tests and ARM64 deployment smoke passed required CI.

The [supported renderer deployment](https://github.com/colophon-group/jobseek/actions/runs/41033456615)
committed source `93a9c49bb063d70b0b0eca8d19c84e96b44d75e0`, immutable image
`sha256:be79daebf84690c5b40bacd9e6181432d82cc2818836a79e6c213167b4fedfff`.
Independent readback verifies the executable, image, source, process generation,
controlled egress, mTLS readiness, **1 GiB memory and zero swap**.

[Serving-deployment correction #11032](https://github.com/colophon-group/jobseek/pull/11032)
replaces the production installer's dormant-only process assumption with explicit
validation of the exact controller and at most four UID/GID 10002 render children,
their parent, fixed commands, resource bounds and egress arguments. The 128 MiB
idle ceiling remains applicable to idle observations; strict dormant qualification
remains in CI. Heap, response, concurrency and retry limits were not raised.
Failed attempts were contained through the original installer; the corrected
workflow restored rendering without a manual container restart or policy override.

The same batch fixes maintenance restoration checks for both
[CSV publication](https://github.com/colophon-group/jobseek/actions/runs/41033639245)
and [currency refresh](https://github.com/colophon-group/jobseek/actions/runs/41033642676).
They select actors from the exact immutable image's runtime capability, including
legacy actors for supported cold reversal, and reject unknown capabilities. Both
workflows passed with zero restoration failures. CSV sync recognized the already
committed current data contract and completed as a no-op. Its earlier failure was
an outer check for four retired Python services after successful publication.
Typesense's earlier HTTP 503 was collection loading during startup; local health
and the exact exporter company-facet query subsequently returned HTTP 200.

## Canonical output, publisher policy and persistence

[Signing correction #11024](https://github.com/colophon-group/jobseek/pull/11024)
selects S3 canonical URI signing and preserves the existing object layout,
including escaped locale values. An independent HMAC verifier covers spaces,
Unicode, percent, plus, slash, brackets and ordinary/empty controls.

All **100 existing failing R2 objects** uploaded through natural drain retries,
with unchanged description hashes, zero failure counts after successful settlement
and matching uploaded canonical hashes. No objects were manually PUT and no retry
state was reset. Subsequent bounded drain observations contain 2,000 upload events
and no PUT HTTP errors. Empty/missing content, reservation behavior, canonical
field presence and final drain/reaper state require the final window readback.

Completed SQL fences include failed/skipped attempts; successful content samples
must explicitly use current zero-failure canonical states. Detected locales may
outnumber localized title variants. `last_enriched_at` and `to_be_enriched` belong
to the separate offline enrichment workflow and do not measure shared Go parsing,
salary, location or taxonomy extraction.

## Freshness and whole-lane resource acceptance

The accepted stability window ran **09:32:25–09:42:09**, with ten samples per
node, fixed source/image/process identities, no restart increments and no OOM.
The Go worker entered with two earlier cumulative restarts during registry
publication; that counter remained unchanged. Renderer and storage generations
were also stable.

| Observation | Legacy control | Native 1125 |
| --- | ---: | ---: |
| Window duration, seconds | 566.8 | 578.2 |
| Successful monitors / details | 726 / 1,454 | 338 / 173 |
| Mean whole-lane CPU cores | 5.43 | 2.97 |
| Mean whole-lane working memory, GiB | 11.97 | 10.79 |
| Successful tasks per CPU second | 0.709 | 0.298 |
| Modeled compute EUR per 1,000 successful tasks | 0.001881 | 0.006552 |

The native window emitted **224,206 monitor inventory items** and 173 detail
outputs. Legacy output-item counters did not advance despite successful task
counters, so legacy inventory-output throughput is **unmeasured**, not zero.
There is no accepted output-normalized density, throughput or cost win. Raw
success-count normalization is 3.48 times more expensive in this observation,
but cannot distinguish differing rich-monitor work and workload mix.

The final sample contains **1,000 recent zero-failure canonical states**, with no
missing titles. Its **999 description rows** are nonempty and uploaded, with no
pending/inflight descriptions in that sample. Salary, occupation, seniority and
technology fields are present in 287, 265, 166 and 270 states respectively.
Forty-seven title/locale cardinality differences have the detected-language
versus localized-title interpretation above. The bounded drain readback has no
PUT HTTP errors; reaper sweeps/lifecycle classifications have no failures.

Freshness acceptance remains open. During the window, monitors successful in the
preceding hour changed from 1,744 to 1,705; due monitors changed from 5,841 to
6,002. All **31 monitors overdue by a day** retain their expected queue/inflight
projection. At the final detail readback, 2,002,260 active eligible postings are
scheduled, 1,954,685 are due, 1,907,288 overdue by a day and 1,649,802 overdue by a
week; 6,451 have never been scraped. There were 1,090 zero-failure detail states
scraped in the preceding hour. These figures prove preserved work and natural
processing, rather than sufficient freshness/capacity.

A targeted readback of 23 boards in low-recent-success provider groups found
22 quarantined and one active. The configured Oracle ADF board is quarantined,
with a future probe deadline and no success since runtime 1125 started. Its
implemented Go HTTP route therefore must not be presented as a fresh production
recovery proof. Preserve quarantine/probe deadlines while distinguishing
pre-existing upstream failures from migration regressions.

Comparison must include crawler, renderer, PostgreSQL and Typesense containers,
with aligned observations and fixed process identities. Report monitor/detail
success counts separately and disclose task-mix, time and shared-storage load.
Task counts can represent different amounts of rich-monitor work; their sum is
not an interchangeable job-output unit. Allocation estimates use net hourly
catalogue price multiplied by max(CPU share, working-memory share), capped at host
capacity. They are not billed savings. Provisioned node sizes and their combined
EUR 0.0624/hour catalogue compute price are unchanged.

Large detail backlogs include deadlines predating the current cutover. Preserve
them and report actual age and natural progress; neither resetting deadlines nor
forcing successful samples proves freshness. Do not claim a zero-overdue SLO or
a cost/throughput improvement from admission tests or an unmatched task mix.

## Production boundary and supported recovery

The Dockerfile's `native` target and production workflow explicitly select Go.
`ordinary-go`, `lease-reaper`, `exporter` and `drain` own queue/fence/scheduling,
canonical persistence, shared enrichment, export and description upload. Go also
handles registry sync, migrations, maintenance, inventory and document conversion.
Browser operations use self-hosted Lightpanda; proven HTTP/API routes stay in Go.
There is no production per-task Python, Playwright or Chromium fallback. Oracle
ADF discovery has a qualified Go HTTP session route before browser dispatch.

Useful Python company setup, labelling, research, tests, offline enrichment and
operator tooling remain isolated from production processing. Legacy Docker
build targets, Compose definitions and compatibility code support offline parity
and the explicit cold-reversal contract; their presence does not authorize mixed
writers or a per-task fallback. Retain these required uses rather than deleting
recovery assets solely because their normal processors are stopped.

The complete [Go-to-legacy cold reversal](https://github.com/colophon-group/jobseek/actions/runs/40899557014)
passed independent image/spec/data/actor checks without mixed ownership. Do not
rewind epoch 404. Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md):
immutable identities, mutation locks, complete writer quiescence and authenticated
restoration. Retain the active generation, durable journal references, explicit
rollback target, five newest verified v3 generations and 48-hour staging grace.
A failed restoration keeps writers stopped. No mutable tags, live rsync, partial
writer restarts, manual SQL/Redis/environment repair or counter resets.

## Remaining delivery and cleanup

1. Classify effective queue deadlines, host circuits and quarantine state for
   the 31 overdue monitors and low-recent-success provider groups. Verify current
   Oracle ADF HTTP and Amazon behavior through bounded read-only evidence;
   neither admission nor retained historical fixtures proves current recovery.
2. Batch any demonstrated migration fixes across affected contracts/types.
   Preserve every enabled board, publisher policy, pacing, quarantine and
   recovery state. Repeat the affected production outcomes after deployment.
3. Resolve the freshness/capacity interpretation and state the density/comparison
   limitations without inventing a zero-backlog SLO or a performance-win target.
   Complete the requirement-by-requirement goal audit with the actual resource
   tradeoffs and supported offline/rollback boundary.

Keep one current checkpoint and conservation boundary plus inputs still needed
for active verification, comparison or supported rollback. Delete consumed raw
captures, superseded diagnostics, compiled test binaries, inactive environments
and unused build caches after each batch. Historical migration evidence need not
be preserved. Protect other contributors' work and production retention.
