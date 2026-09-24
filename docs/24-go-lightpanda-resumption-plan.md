# Go + Lightpanda migration: resumption plan

Status: implementation checkpoint, 2026-09-24. The initial review used
`origin/main` `6abfa52e1b061f2898a46355c48105accd1c01f9`; the B0 producer
implementation merged as `dcdc407ba836d75ec93e7416faa5f9fdabd41001`.
The fixture admission gate has passed; [#8648](https://github.com/colophon-group/jobseek/issues/8648)
tracks current production c1 admission evidence.

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
  effort to bounded, independently useful pilots. The full rewrite checklist,
  10M-board projection, zero-Chromium premise, and exact evidence chains in
  [the older migration design](23-go-lightpanda-migration.md) are historical,
  not current prerequisites.
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
