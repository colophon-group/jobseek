# Go + Lightpanda migration: resumption plan

Status: implementation checkpoint, 2026-09-24. The initial review used
`origin/main` `6abfa52e1b061f2898a46355c48105accd1c01f9`; the B0 producer
implementation merged as `dcdc407ba836d75ec93e7416faa5f9fdabd41001`.
The fixture admission gate has passed; [#8648](https://github.com/colophon-group/jobseek/issues/8648)
tracks current production c1 admission evidence.

## Implementation checkpoint: 2026-09-25 16:15 UTC

The bounded Go sitemap parser from the read-only pilot is now packaged for
the crawler image behind `SITEMAP_GO_BOARD_IDS`. It accepts only an explicit
same-origin HTTPS `urlset` and runs the same Python URL filter, allowlist, and
transform stages before the existing board writer. It is default-off; an
unsupported index or changed configuration fails closed. The accepted
[production shadow](https://github.com/colophon-group/jobseek/issues/8641)
previously matched Python's URL counts and hashes for Acosta/Dee Set and
Verity Breezy. Select Verity's exact board ID
`c4779214-ef92-4261-98fb-ae64f264fd23` only after this crawler release is
deployed, then observe its natural Go output and active PostgreSQL URL digest.
Do not force the board due or send a duplicate origin request.

The unrelated crawler v0.13.860 deploy from `bcf2d84c4` failed before image
change because c1 was active. Production remains on `2478244da`; c1 epoch 68
and its 21 exact selectors remain active. Before the next crawler deploy, use
the supported c1 cold rollback, clear all 21 selectors at exact revision
`2478244da10230814e7a045c585f547e5d200f0e` under the host mutation lock,
and only then deploy. Restage the desired selectors at the promoted revision
and reactivate c1. The full [#7966](https://github.com/colophon-group/jobseek/issues/7966)
completion goal remains open while Python and Chromium own production work.

## Production checkpoint: 2026-09-25 15:16 UTC

[PR #10024](https://github.com/colophon-group/jobseek/pull/10024) merged as
`c09c1d519` and [deploy run 36148928512](https://github.com/colophon-group/jobseek/actions/runs/36148928512)
promoted crawler v0.13.858. The Go Workable detail scraper is installed but
defaults off. A naturally scheduled KI Insurance Python detail response was
captured without another origin request. Its 5,010 exact bytes, SHA-256
`0b7b1f982990b630b5b1f9ef51c28437b520761af8e849b5610ae6ac86b0f0fd`,
produced identical Python and Go values for all seven populated detail fields.
The Python scrape succeeded. KI Insurance has 18 active jobs, zero board
failures, and no browser requirement.

The supported c1 rollback retired epoch 65, restored all five schedules,
and confirmed zero dropped tasks and write fences. Twenty exact selectors
were cleared under the mutation lock; 21 were staged at exact release
`c09c1d5191f779280e088c6b6131a1c66426835f` with
`/tmp/jobseek-post-workable-go-detail-pilot-selectors.py` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. C1 reactivated at epoch 66 with
all five schedules; services are healthy. The added Workable Go detail selector
targets only KI Insurance board `aef95fd2-55ea-43cd-9aa6-9bd541c2bc4e`.
Its natural Go scrape and persisted content readback are pending. The list
capture selector still targets four Python Workable boards; natural scheduled
responses are next due around 15:40–15:57 UTC. No due scores or extra origin
requests were forced. Personio 5% selects Silverflow and newly eligible
Eraneos Germany (five stable 21-job runs); Eraneos Go output is pending.
Before any further deploy or selector mutation, cold-rollback c1 and clear
those exact 21 selectors with that script, revision, and Kandou URL.

The next strict Lever code slice resolves five of the six remaining Python
boards: two explicit dotted tokens, two explicit tokens on canonical company
career URLs, and one unused `company` metadata value matching its derived
token. The sixth, Volta Medical, retains a JSON-LD detail scraper and remains
Python-owned. Live output and database effects for the five new routes remain
to be observed after deployment; this change cannot establish the full
[#7966](https://github.com/colophon-group/jobseek/issues/7966) gate while
Python and Chromium still own production work.

## Production checkpoint: 2026-09-25 14:26 UTC

[PR #10023](https://github.com/colophon-group/jobseek/pull/10023) merged as
`6c993efe5` and [deploy run 36145740751](https://github.com/colophon-group/jobseek/actions/runs/36145740751)
promoted crawler v0.13.857. The route census changed from 165 to 189 Go
Lever boards out of 195 enabled: 20 canonical direct URL boards now derive
their missing token as Python does, and four EU boards now derive the missing
region as Python does. Six Lever boards retain Python routing because their
configuration does not satisfy the strict Go guard. All 24 newly eligible
boards had zero consecutive failures before their first natural selected Go
cycle; live output and database readback for this cohort are still pending.
`LEVER_GO_PERCENT=0` reverses the default.

Supported B0 c1 cold rollback retired epoch 61 and restored all five retained
schedules before deployment. The 18 old selectors were cleared under the host
mutation lock. After deployment, the same 18 were staged with
`/tmp/jobseek-post-workable-capture-selectors.py` at revision
`6c993efe5780e38ed5f90730dbe881f52c485501` and Kandou URL
`https://kandou.bamboohr.com/careers/310`; c1 reactivated with five
schedules at epoch 62. Workers, browser, drain, producer, executor, claimant,
and Redis are healthy. Before another crawler deployment, cold-rollback c1
and clear these exact selectors with the same script, revision, and URL.
Workable list capture on natural Python schedules remains pending; do not
force due scores or duplicate origin traffic. C2 Kandou stays dark.

[Draft PR #10024](https://github.com/colophon-group/jobseek/pull/10024)
adds default-off Go Workable detail extraction and passive capture for exact
scheduled detail jobs and Workable Markdown/public API list fallback bodies.
It is not deployed or selected. The full [#7966](https://github.com/colophon-group/jobseek/issues/7966)
completion gate remains unmet while Python and Chromium own production work.

## Production checkpoint: 2026-09-25 13:41 UTC

[PR #10022](https://github.com/colophon-group/jobseek/pull/10022) merged as
`6c8b7179b` and [deploy run 36140762256](https://github.com/colophon-group/jobseek/actions/runs/36140762256)
promoted crawler v0.13.856. It adds a default-off Go Workable list monitor
and passive capture of existing Python list responses. Before deployment,
supported c1 cold rollback retired epoch 59 and restored all five retained
schedules; the 17 exact old selectors were cleared under the host mutation
lock. After deployment, 18 exact selectors were staged with
`/tmp/jobseek-post-workable-capture-selectors.py` at revision
`6c8b7179b91265336d25cee32275ea8f1bebbb9a` and Kandou URL
`https://kandou.bamboohr.com/careers/310`. The added selector captures
Workable responses for `pix4d,debiopharm,unit8,hack-the-box-ltd` on their
natural Python runs. No Workable board routes to Go yet. Supported c1
reactivation selected five schedules at epoch 60; workers, browser, drain,
producer, executor, claimant, and Redis are healthy. C2 Kandou stays dark.
Before any later crawler deployment, cold-rollback c1 and clear all 18
selectors with that exact script, revision, and Kandou URL.

Silverflow Personio completed its first natural selected Go monitor run at
13:24 UTC: four URLs, two responses, zero monitor or board failures. Its
sorted URL digest
`2a7048973d0c0e572788718e93234a4e1a10d8c8b21ad43a76f6aff49960a1ba`
matched all four active PostgreSQL rows after the existing writer ran. The
earlier exact-byte EN/DE replay matched all four full rich dictionaries.
This is a live monitor and persistence checkpoint, not a whole-lane resource
measurement. The first Mobiliar SuccessFactors Go RSS run is still queued;
do not force it or duplicate origin traffic.

[PR #10023](https://github.com/colophon-group/jobseek/pull/10023) is open
from the new main. It lets the Go Lever runtime derive token and EU region
from a canonical direct board URL just as Python already does. Twenty current
CSV boards have this strict tokenless shape. Merge it only after its gates
pass and after preserving the B0 cold rollback / selector procedure above.
The fleet remains largely Python-owned and does not satisfy
[#7966](https://github.com/colophon-group/jobseek/issues/7966).

## Production checkpoint: 2026-09-25 12:48 UTC

[PR #10021](https://github.com/colophon-group/jobseek/pull/10021) merged as
`e9a1421d4` and [deploy run 36134079773](https://github.com/colophon-group/jobseek/actions/runs/36134079773)
successfully promoted crawler v0.13.855. Strict direct Lever boards now use Go
by default; `LEVER_GO_PERCENT=0` remains the route reversal. Supported B0 c1
was cold-rolled back at epoch 57, restoring all five retained schedules with
no loss, then reactivated at epoch 58 after deployment. C2 Kandou stays dark.
All workers, browser, drain, producer, executor, claimant, and Redis were
healthy after the transition.

Seventeen temporary selectors were staged under the host mutation lock using
`/tmp/jobseek-post-lever-default-selectors.py` with exact revision `e9a1421d4`
and Kandou rendered-DOM capture URL `/careers/310`. The route census is 1,471
Go selections and 6,394 Python selections across 7,865 enabled boards. Before
another crawler deployment, cold-rollback c1 and clear those exact selectors
with the same script, full revision and Kandou URL; then stage the desired
post-release selectors and reactivate c1. The prior 20 natural Go
Greenhouse/Lever/Workday cycles had zero failures and exact active PostgreSQL
URL counts and digests. Their rich fields and whole-lane resource efficiency
were not established by that readback.

Silverflow Personio's natural Python monitor captured EN and DE XML at 12:18
UTC. Offline Go and Python replay of the exact same bytes matched all four
ordered rich-job dictionaries, including descriptions and German
localizations. The four active PostgreSQL URLs matched digest
`2a7048973d0c0e572788718e93234a4e1a10d8c8b21ad43a76f6aff49960a1ba`,
with zero board failures. Its exact Go selector is now active; the first
natural Go cycle is due after 13:18 UTC. The first Mobiliar SuccessFactors
Go RSS cycle remains queued. The Go Workable URL monitor stays default-off
pending live response capture and admission; its existing detail scraper
still runs in Python. None of these steps closes [#7966](https://github.com/colophon-group/jobseek/issues/7966).

## Production checkpoint: 2026-09-25 12:00 UTC

The latest crawler release is `4c620132c` (v0.13.854), deployed by
[run 36128889906](https://github.com/colophon-group/jobseek/actions/runs/36128889906).
Supported B0 c1 is active at epoch 56 with five retained schedules; c2
Kandou remains dark. Temporary selectors route 1,470 of 7,865 enabled boards
to Go monitors: 1,275 Greenhouse, 165 Lever, 21 Ashby, and nine individually
selected boards. Fourteen natural Greenhouse/Lever batch cycles since 11:43
completed with zero failures and exact active database URL count/digest
readback. This checks persistence of the selected URL inventory, not every
rich field or the whole crawler lane. The same-byte Mobiliar SuccessFactors
RSS replay matched all 71 rich jobs before its exact Go selector was staged;
its first natural Go cycle remains pending. A Silverflow Personio response is
being captured passively on its next normal schedule. Do not force either
board due or send a second request to its origin.

The next code release makes the strict 165-board Lever cohort Go by default,
while `LEVER_GO_PERCENT=0` remains the immediate route reversal. Before any
crawler deployment, use the supported B0 c1 cold rollback, clear temporary
host selectors under the mutation lock, and verify the host environment
matches the release snapshot. After deployment, stage desired selectors and
reactivate c1. Python still owns most monitors and the board writer; this
checkpoint does not satisfy [#7966](https://github.com/colophon-group/jobseek/issues/7966).

The delivery goal is a complete Go crawler using self-hosted Lightpanda for
browser work and Go HTTP/API execution where that removes a browser need.
Python, Playwright, and Chromium leave production after every enabled profile
has equivalent output and the Go runtime owns scheduling, persistence, drain,
export, maintenance, and configuration sync. Temporary Chromium assignments
remain explicit during migration; eliminating them is a completion task, not
an admission condition for the first B0 cohort.

## Production checkpoint: 2026-09-24 20:20 UTC

This checkpoint supersedes the deployment and owner statements below. The
latest code baseline for further work is `origin/main` `3e9e737c4`.
[PR #9991](https://github.com/colophon-group/jobseek/pull/9991) merged the
guarded Go Typesense CDC exporter as `2ade431d9` and
[deploy run 36052100515](https://github.com/colophon-group/jobseek/actions/runs/36052100515)
successfully promoted that revision. The first deploy attempt failed before
the image changed because a temporary Elastic capture variable in the host
environment differed from the committed release. Its rollback restored the
old image and workers; the successful retry ran with both temporary pilot
variables removed. Clear pilot variables **entirely** before each future
deploy and check host environment attestation, excluding only `COMPOSE_FILE`.

The fenced production `python -> go` Typesense owner transfer succeeded at
about 20:13 UTC. Python exited on its ownership check, and the same exporter
service restarted under the Go entrypoint. At 20:19 UTC, Go had acknowledged
566 live documents with zero rejected documents, export errors, or unknown CDC
writers. The Typesense downstream availability metric was healthy, the process
was running without an OOM, and its cgroup used about 57 MiB of its 256 MiB
limit. The owner query returned `go`. This is a live ownership and correctness
checkpoint, not an equal-workload resource comparison for the whole crawler.
Keep the Go owner unless a concrete failure requires the documented reverse
transfer; never rewind the cursor.

The supported c1 rollback before deployment restored five due schedules to
Python at retired epoch 27. After the successful deploy, the one-board
Elevance Workday Go selector was staged again and confirmed inside a recreated
HTTP worker. The supported B0 c1 activation selected five boards at routing
epoch **28** and wrote `/home/deploy/.lightpanda-b0-active-v1` for the
`2ade431d9` image. Producer, executor, claimant, workers, browser, and drain
were healthy; the host mutation lock was free. C1's first retained detail is
due 2026-09-25 00:26 UTC. C2 remains dark because the Kandou required-title
mismatch has not been resolved. The Elastic natural response capture has not
arrived; its temporary capture variable is currently absent. Before another
crawler deploy, cold-rollback c1 with the supported wrapper, remove the
`WORKDAY_GO_BOARD_ID` line under the host mutation lock, and verify release
environment attestation. Reactivate c1 only after that deploy succeeds.

The completion gate remains [#7966](https://github.com/colophon-group/jobseek/issues/7966):
most enabled boards still use the Python worker and Chromium routes. Next,
observe c1's retained natural detail, continue the exclusive Workday monitor
cohort with same-input resource evidence, resolve a concrete Lightpanda
capability or Go HTTP profile, and replace the remaining Python runtime stages
and board families. Do not infer whole-fleet efficiency from these pilots.

## Execution checkpoint: 2026-09-24

This checkpoint supersedes the older epoch and pending-run statements below.
On the prior deployed revision `1f8e9113bfc4730ca9ba9ada183dbdd507827fa4`,
two natural Elevance Go Workday cycles completed at 16:18 and 17:25 UTC.
They found 308 and 309 URLs in 16 requests each with zero transport errors.
Both cycles' sorted URL digests matched their exact database readbacks after
the existing Python board writer ran. The publisher changed between cycles;
these are live persistence results, not same-input Python-versus-Go resource
comparisons. [#7955](https://github.com/colophon-group/jobseek/issues/7955#issuecomment-5818925063)
records the second run.

[PR #9989](https://github.com/colophon-group/jobseek/pull/9989) merged as
`d0077bdc3459c5f2380caae8e55b33a387b811d3` and
[deployed successfully](https://github.com/colophon-group/jobseek/actions/runs/36036386828).
It combines the default-off rich Elastic Go Greenhouse monitor, one scheduled
Elastic response capture, Kandou rendered-DOM capture, Booking main-response
and DOM capture, and the c2 legacy scrape-enqueue owner guard. Earlier draft
PRs #9979, #9987, and #9944 were closed as superseded. Synthetic same-byte
Greenhouse Go/Python field replay passes; live Elastic capture and whole-lane
measurement still gate exclusive Go activation. Kandou's intermittent
required-title failure keeps c2 dark.

For this deploy, supported c1 cold rollback retired epoch 25 and restored all
five future due schedules to Python before the crawler image changed. The
temporary one-board Workday selector line was removed entirely under the host
mutation lock. After deployment, the one-board selector and Elastic capture
path were staged under that lock, and supported c1 reactivation transferred
the same five due scores to Go at routing epoch **26**. The active receipt,
producer owner, and route agree; no B0 job is inflight or dead, all services
are healthy, and the mutation lock is free. The first existing c1 detail is
due 2026-09-25 00:26 UTC, so live Lightpanda detail output and production
whole-lane resources remain unmeasured. Remove the Workday selector line
entirely before any later crawler deploy, and cold-rollback c1 first.
[#8648](https://github.com/colophon-group/jobseek/issues/8648#issuecomment-5819378524)
records the transition. The fleet remains Python-owned outside the named
pilots; zero Python and zero Chromium are not achieved.

At 18:30 UTC a third natural Elevance Go Workday cycle found 311 URLs in 16
requests with zero transport errors. Its sorted URL digest
`5eb7dd7d88ec5730e840c720acb475f094106b3aa72c0b2b772165c9a62aa099`
matched the exact 311 active PostgreSQL rows. Three new details were
scraped successfully by the existing Python detail owner. The publisher
changed again, so this remains output/persistence evidence for the Go list
monitor rather than a whole-lane resource comparison.

## Go Typesense exporter slice

An isolated branch based on deployed `d0077bdc` now contains a default-dark
Go Typesense CDC exporter. It uses the current Python export cursor encoding,
exact CDC cutoff SQL, shared PostgreSQL advisory fence, taxonomy inputs,
company JOIN, per-document Typesense acknowledgements, and bounded downstream
backoff. It checks a durable `export_owner:typesense:job_posting` row inside
the fence before every import. A missing row means Python; Python checks the
same row inside the same fence. Neither runtime can import under the other's
ownership. The owner-aware exporter entrypoint chooses the process at each
container start, including after an ordinary deployment rewrites `.env`.
The Go process retains the existing exporter staleness, Typesense health,
CDC cutoff, error, and bounded Redis queue-depth metrics on port 9093.

The production Python exporter is still authoritative. A read-only snapshot
of 200 recent production postings on 24 September projected identical
Python and Go Typesense documents across all fields. Go loaded its own live
taxonomy maps and read the exact same database cutoff as Python. One
occupation ancestor array initially differed only in order; both runtimes
now emit stable sorted order, and the 200-row rerun matched. The comparison
ran in a separate, memory-bounded container and sent no publisher or Typesense
requests. The production exporter restarted once after an initial comparison
attempt exceeded its 256 MiB container limit; it recovered and resumed
exporting. Do not run this comparison inside the live exporter again. The
Go writer has not imported a production document or advanced the cursor.

The handoff command is an explicit compare-and-swap:

```bash
cd /home/deploy
flock -x /run/lock/jobseek-crawler-mutation.lock \
  docker compose run --rm --no-deps \
  -e GO_TYPESENSE_EXPORTER_TRANSFER=1 exporter \
  /usr/local/bin/go-typesense-exporter --transfer-owner python go
```

Run it only after deploying the owner-aware image with Python still selected,
checking the cursor and index lag, and holding the host crawler mutation
lock. The command waits for the in-flight Python tick through the shared
fence, verifies the existing cursor, and records Go ownership. The Python
process exits on its next ownership check; Compose restarts the exporter
service, whose entrypoint selects Go. Confirm the process, port 9093 metrics,
cursor progress, acknowledgements, index lag, and absence of document drops.
If the Go process fails or those checks fail, reverse ownership with the same
command ending `--transfer-owner go python`; the Go process exits and the
entrypoint restarts Python from the retained cursor. Never rewind the cursor.
Before the first owner-aware image deploy, cold-rollback c1 and remove the
temporary Workday selector line from the host environment as described above;
reactivate c1 only after the new release passes its normal deployment gates.

## Current state

- [#7935](https://github.com/colophon-group/jobseek/issues/7935) reset the
  initial effort to bounded, independently useful pilots. The 10M-board
  projection and first-pilot gates in
  [the older migration design](23-go-lightpanda-migration.md) are historical.
  The owner subsequently made complete retirement of Python, Playwright, and
  Chromium the delivery goal in [#7966](https://github.com/colophon-group/jobseek/issues/7966).
  Its enabled-fleet and whole-lane completion criteria govern final cutover.
- The bounded Go sitemap worker and read-only production shadow landed through
  [#8644](https://github.com/colophon-group/jobseek/pull/8644); the fleet
  benchmark landed through [#8660](https://github.com/colophon-group/jobseek/pull/8660)
  and [#8694](https://github.com/colophon-group/jobseek/pull/8694). Python
  still owns sitemap schedules and writes. The earlier
  [#8461](https://github.com/colophon-group/jobseek/pull/8461) and
  [#8524](https://github.com/colophon-group/jobseek/pull/8524) drafts are
  closed without merge; they are evidence, not candidate branches.
- [#8881](https://github.com/colophon-group/jobseek/pull/8881) merged the
  bounded B0 Go supervisor, Lightpanda renderer route, Python database-only
  executor, and explicit c1/c4 cutover tooling. Ordinary deployment is dark.
  Production c1 uses the separate, receipt-guarded activation overlay.
- [#8738](https://github.com/colophon-group/jobseek/issues/8738) landed the
  bounded Go producer authority through [#9874](https://github.com/colophon-group/jobseek/pull/9874).
  The 48-file predecessor at `d6d583515` was reassessed against concrete
  crash and ownership failures and reconciled with current queue semantics.
  About half of its addition was test code. The exact merged revision passed
  required CI and the crawler deploy gate; [deploy run 35845382807](https://github.com/colophon-group/jobseek/actions/runs/35845382807)
  succeeded with only the dark claimant in the ordinary service list. There is
  no evidence of enabled B0 traffic in that deployment.
- [#8648](https://github.com/colophon-group/jobseek/issues/8648) owns the
  remaining whole-lane admission. [PR #9880](https://github.com/colophon-group/jobseek/pull/9880)
  merged the real-producer harness. Its exact-source [ARM64 fixture run](https://github.com/colophon-group/jobseek/actions/runs/35861227140)
  exercised the real Go producer in all 16 arms with exact output, request,
  persistence, and queue parity. Median correct-URL density improved 3.42×
  at c1 and 6.71× at c4; peak memory and completion latency fell. CPU use
  rose from 12.88 to 23.06 seconds at c1 and 17.53 to 23.88 seconds at c4
  across the common due and retained measurement window. About 20 CPU seconds
  per arm came from the transitional Python executor. Production c1 reached
  routing epoch 14 with five Go-owned schedules. Its lightweight executor
  health probe cut measured idle CPU from 10 to 1 seconds per 20-second
  window. A cold rollback returned all five schedules to the legacy queue
  before [#9934's crawler deploy](https://github.com/colophon-group/jobseek/actions/runs/35903707755);
  the supported cutover reactivated c1 at epoch 16 afterward. For the
  [Go drain deploy](https://github.com/colophon-group/jobseek/actions/runs/35912138580),
  c1 was cold-rolled back again at retired epoch 17, with all five exact due
  scores restored, then reactivated on revision `4a88deaf` at epoch 18.
  Production B0 output, requests, and whole-lane capacity still need
  measurement. The public sitemap run in #7935 was inconclusive because the live source changed
  between arms; it is not a Python-versus-Go verdict.
- [#7959](https://github.com/colophon-group/jobseek/issues/7959) admitted
  pinned Lightpanda 0.4.0 for narrow B0/B1 nonproduction use. Its broader
  compatibility results do not justify moving interactive, frame, identity,
  or API-sniffer profiles. Chromium remains an explicit compatibility owner
  where those capabilities are unproven.
- [PR #9932](https://github.com/colophon-group/jobseek/pull/9932) merged and
  deployed the exclusive Go R2 drain. A counterbalanced 2,000-description
  ARM64 fixture preserved every object and database pointer while reducing
  CPU 3.75–3.86× and retained memory 9.5–10.1×. A
  [200-second production sample](https://github.com/colophon-group/jobseek/issues/7945#issuecomment-5802172400)
  observed 247 successful uploads, 17.72 ms CPU per upload, 21.9 MiB final
  cgroup memory, and exact R2/DB/pointer readback on five samples. The prior natural
  Python sample used 22.14 ms CPU per upload and about 118 MiB memory; the
  input sizes differed, so only the fixture is an equal-input CPU comparison.
  [PR #9935](https://github.com/colophon-group/jobseek/pull/9935) prepares
  the three validated production B0 origins; it remains undeployed pending
  c1's first due work.

## Next slice: one real Go-owned B0 cohort

1. **Producer implementation landed.** The producer successor was
   assembled separately from the held #8648 admission harness. It keeps only
   the bounded producer, four Go claim slots, existing Redis/SQL fences,
   the Python database-only executor, and cold rollback. The large patch
   addressed specific crash and ownership failures; it is not a template for
   later family ports.
2. **Keep authority and reversal correct before traffic.** A selected posting
   must have exactly one schedule and one owner. The existing routing epoch
   must be monotonic across activation and rollback, including restored Redis
   or host snapshots. Cover lease loss, failed render/executor transitions,
   Redis persistence boundaries, pending-receipt reboot, and rollback from
  current PostgreSQL schedule truth. Bound task occupancy for the next cohort;
   do not build generic compaction or a new distributed scheduler for it.
3. **Dark default; c1 overlay is receipt guarded.** The merged revision passed real
   Redis/PostgreSQL transitions, container startup, and the required CI/deploy
   gates. The ordinary deploy lists the old Python/Chromium services and dark
   claimant. The c1 overlay has been cold-rolled back and reactivated around
   crawler deploys without changing its five retained due times. The current
   active epoch is 18 on revision `4a88deaf`. Check the
   current receipt, Redis owner, and host mutation lock before any mutation;
   [#8648](https://github.com/colophon-group/jobseek/issues/8648) records the
   current routing epoch. No reviewer or operator approval is an additional
   gate once the stated checks pass.
4. **Whole-lane fixture complete.** The merged harness exercised the producer
   with identical immutable fixture inputs and equal 1.5 GiB whole-lane
   budgets. Its report includes exact canonical output, terminal state,
   requests, queue conservation, paired CPU seconds, peak/retained RSS,
   elapsed time, and correct terminal URLs per GiB-minute. The measured
   density improvement and identical output meet the fixture decision rule;
   no arbitrary 1.25x hurdle or approval is required. Small live-source
   parity and production host telemetry remain part of c1 admission. Treat
   live-input drift as inconclusive.

   The held `fix-crawler/go-b0-admission` branch at `266725d45` is source
   material, not a merge candidate. It bypassed the Go producer via Python
   queue mutation. The merged harness retained its immutable fixture, PKI,
   output comparison, and external cgroup sampler, then fed the candidate
   through the Go producer's Unix socket. It counted the producer in the
   candidate cgroup and budget: 32 MiB producer,
   96 MiB supervisor, 384 MiB executor, 1024 MiB renderer (1536 MiB total),
   against a 1536 MiB control. The isolated counterbalanced c1/c4 report
   passed. This is fixture evidence; production c1 has not processed a due
   task yet.
5. **Observe c1, then expand to three origins if the numbers hold.** C1 has
   exclusive Go/Lightpanda ownership for one low-rate origin.
   Verify no duplicate origin request, stale write, lost schedule, unsupported
   capability, or same-task Chromium fallback. Expand to the other two
   currently validated origins only if c1 remains correct and the combined
   lane stays inside its memory and freshness limits. Judge complete scheduled
   work, exact output and queue effects, request conservation, CPU, and memory
   on the actual cohort. Record sample size and uncertainty; cold-rollback on
   a material regression. The
   fixed c4 benchmark manifest contains a `suspect` board. Production cutover
   uses a separate c3 manifest with only the three validated origins; c4 stays
   available to the frozen four-origin fixture and is rejected by the production
   activation wrapper. Never activate the suspect board just to reach four.

## Decision after B0

If the admitted multi-origin lane preserves output and uses fewer whole-lane
resources under the equal workload, choose one additional bounded family. The
Go HTTP sitemap worker already on `main` is the natural candidate, subject to
a stable-source cohort and the same exclusive ownership
and publisher-policy checks. Port only the semantics required by that cohort.
If the B0 result is weak or inconclusive, keep the working Python path and
resolve the measured limitation before expanding.

After B0, migrate one measured family or independently replaceable process at
a time: Go HTTP monitors and detail fetches, remaining monitor/scraper
families and enrichment, export/maintenance, then configuration sync.
For browser profiles, use pinned Lightpanda replay or move the origin to a
proved HTTP/API route. Track every temporary Chromium assignment until none
remain. Complete the migration only when the enabled fleet has zero Python or
Chromium crawl ownership, parity and resource measurements pass for its actual
workload, and the legacy production services and rollback window are retired.

Do not make the old broad issue list a dependency graph again. In particular,
defer catalog/outbox replacement, queue v2, dynamic sharding, generic browser
APIs, every ATS/monitor family, and Chromium retirement as *early pilot
prerequisites*. Revisit a Lightpanda capability when a specific configured
profile and pinned Linux build provide a concrete reason and exact replay
evidence. A new abstraction is justified only by a measured migration need.

## Issue ownership

- #7935: concise epic and the current decision record.
- #8738: completed implementation of exclusive Go B0 producer/ownership and
  cold reversal. #8881 is the merged dark foundation; #9874 merged the
  producer and deployed dark. Traffic admission remains #8648.
- #8648: whole-lane output parity and measured resource efficiency, followed
  by c1 observation and the validated three-origin expansion.
- #7941: cohort switch/reversal tracking folded into #8738; avoid a parallel
  generic cutover program.
- #7938: parked queue-v2 contract; it does not gate B0.
- #7962/#7963 and other family-port issues: deferred until measured cohort
  demand justifies a bounded slice.
- #7966: final zero-Python/Playwright/Chromium completion and retirement gate,
  reopened for the complete migration goal; it does not block B0 admission.
