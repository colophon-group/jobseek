# Go + Lightpanda migration: resumption plan

Status: planning checkpoint, 2026-09-23. This plan is based on `origin/main`
`6abfa52e1b061f2898a46355c48105accd1c01f9` and the linked issue/PR
history. It authorizes no production activation.

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
  The issue record does not establish that production B0 traffic has been
  activated; verify live state before any future cutover.
- [#8738](https://github.com/colophon-group/jobseek/issues/8738) records an
  additional Go producer-authority candidate at local commit `d6d583515`.
  It is unpushed and unmerged. Its 48-file, 12,172-line addition diverged from
  current `main`; use it as a read-only donor and re-evaluate its scope rather
  than rebasing or merging it wholesale.
- [#8648](https://github.com/colophon-group/jobseek/issues/8648) owns the
  remaining whole-lane admission. Its harness candidate was held until it
  could exercise the real Go producer. The public sitemap run in #7935 was
  inconclusive because the live source changed between arms; it is not a
  Python-versus-Go verdict.
- [#7959](https://github.com/colophon-group/jobseek/issues/7959) admitted
  pinned Lightpanda 0.4.0 for narrow B0/B1 nonproduction use. Its broader
  compatibility results do not justify moving interactive, frame, identity,
  or API-sniffer profiles. Chromium remains an explicit compatibility owner
  where those capabilities are unproven.

## Next slice: one real Go-owned B0 cohort

1. **Reconcile the implementation on fresh `main`.** Compare the merged dark
   lane with `d6d583515` and the held #8648 harness. Keep only the operations
   needed for c1/c4: one exclusive producer, four bounded Go claim slots,
   existing Redis/SQL fences, the Python database-only executor, and cold
   rollback. Split implementation and admission harness into small PRs with
   reproducible test commands. Treat the old branch as source material.
2. **Make authority and reversal correct before traffic.** A selected posting
   must have exactly one schedule and one owner. The existing routing epoch
   must be monotonic across activation and rollback, including restored Redis
   or host snapshots. Cover lease loss, failed render/executor transitions,
   Redis persistence boundaries, pending-receipt reboot, and rollback from
   current PostgreSQL schedule truth. Bound task occupancy for the c4 cohort;
   do not build generic compaction or a new distributed scheduler for it.
3. **Land the minimal producer dark.** On the exact current head, verify the
   real Redis/PostgreSQL transitions and the service startup path with tests
   that exercise ownership, crash recovery, and resource bounds. Deploy dark
   and confirm the old Python/Chromium path still owns all work. No reviewer or
   operator approval is an additional gate once the stated checks pass.
4. **Run #8648 against the actual whole lane.** Reconcile its held harness
   with the producer. Use identical immutable fixture inputs and equal 1.5 GiB
   whole-lane budgets, including the producer in Go's budget. Compare exact
   canonical output, terminal state, requests, and queue conservation first.
   Then report paired whole-lane CPU seconds, peak/steady RSS, elapsed time,
   and correct terminal URLs per GiB-minute. A repeatable resource improvement
   with the same output and no material freshness or request regression is
   sufficient to proceed; no arbitrary 1.25x hurdle or approval is required.
   Treat live-input drift as inconclusive. Run small live-source parity without
   production writes; do not repeat fixed-order public benchmarks for a
   favorable result.
5. **Admit c1, then c4 if the numbers hold.** After #8648 demonstrates output
   parity and resource efficiency, use the cutover to give one low-rate origin
   exclusive Go/Lightpanda ownership.
   Verify no duplicate origin request, stale write, lost schedule, unsupported
   capability, or same-task Chromium fallback. Expand to four origins only if
   c1 remains correct and the combined lane stays inside its memory and
   freshness limits. Observe at least seven scheduling cycles and 200 terminal
   URLs across the admitted cohort; otherwise cold-rollback and retain the
   evidence.

## Decision after B0

If the admitted c4 lane preserves output and uses fewer whole-lane resources
under the equal workload, choose one additional bounded family. The Go HTTP
sitemap worker already on `main` is the natural
candidate, subject to a stable-source cohort and the same exclusive ownership
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
- #8738: implementation of exclusive Go B0 producer/ownership and cold
  reversal. #8881 is the merged dark foundation; the unmerged local candidate
  is not accepted implementation.
- #8648: whole-lane output parity and measured resource efficiency, followed
  by c1/c4 admission after the real producer lands.
- #7941: cohort switch/reversal tracking folded into #8738; avoid a parallel
  generic cutover program.
- #7938: parked queue-v2 contract; it does not gate B0.
- #7962/#7963 and other family-port issues: deferred until measured cohort
  demand justifies a bounded slice.
- #7966: final zero-Python/Playwright/Chromium completion and retirement gate,
  reopened for the complete migration goal; it does not block B0 admission.
