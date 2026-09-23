# Go + Lightpanda migration: resumption plan

Status: implementation checkpoint, 2026-09-23. The initial review used
`origin/main` `6abfa52e1b061f2898a46355c48105accd1c01f9`; the B0 producer
implementation merged as `dcdc407ba836d75ec93e7416faa5f9fdabd41001`.
The fixture admission gate has passed; [#8648](https://github.com/colophon-group/jobseek/issues/8648)
tracks current production evidence and the active c1 cohort.

The delivery goal is a complete Go crawler using self-hosted Lightpanda for
browser work and Go HTTP/API execution where that removes a browser need.
Python, Playwright, and Chromium leave production after every enabled profile
has equivalent output and the Go runtime owns scheduling, persistence, drain,
export, maintenance, and configuration sync. Temporary Chromium assignments
remain explicit during migration; eliminating them is a completion task, not
an admission condition for the first B0 cohort.

## Current state

- [#7935](https://github.com/colophon-group/jobseek/issues/7935) reset the
  effort to bounded, independently useful pilots. The full rewrite checklist,
  10M-board projection, zero-Chromium premise, and exact evidence chains in
  [the older migration design](23-go-lightpanda-migration.md) are historical,
  not current prerequisites.
- The Go sitemap worker and its bounded resource evidence landed through
  [#8660](https://github.com/colophon-group/jobseek/pull/8660) and
  [#8694](https://github.com/colophon-group/jobseek/pull/8694). The earlier
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
  per arm came from the transitional Python executor. The production c1 lane
  is active at routing epoch 12 with five Go-owned schedules. Its lightweight
  executor health probe cut measured idle CPU from 10 to 1 seconds per
  20-second window. No task has been due yet, so production output, requests,
  and whole-lane capacity still need measurement. The public
  sitemap run in #7935 was inconclusive because the live source changed
  between arms; it is not a Python-versus-Go verdict.
- [#7959](https://github.com/colophon-group/jobseek/issues/7959) admitted
  pinned Lightpanda 0.4.0 for narrow B0/B1 nonproduction use. Its broader
  compatibility results do not justify moving interactive, frame, identity,
  or API-sniffer profiles. Chromium remains an explicit compatibility owner
  where those capabilities are unproven.

## Next slice: one real Go-owned B0 cohort

1. **Producer implementation landed.** The producer successor was
   assembled separately from the held #8648 admission harness. It keeps only
   the c1/c4 producer, four bounded Go claim slots, existing Redis/SQL fences,
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
3. **Dark default and c1 overlay active.** The merged revision passed real
   Redis/PostgreSQL transitions, container startup, and the required CI/deploy
   gates. The ordinary deploy lists the old Python/Chromium services and dark
   claimant. The c1 overlay at epoch 12 runs the enabled producer, executor,
   and claimant with an active receipt. Check that receipt and Redis owner
   before any mutation. No reviewer or operator approval is an additional
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
families and enrichment, drain/export/maintenance, then configuration sync.
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
