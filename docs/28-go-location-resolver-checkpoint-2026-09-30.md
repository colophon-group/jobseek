# Go location resolver and salary continuation checkpoint

Continuation of the [full migration plan](27-go-lightpanda-continuation-plan.md).
The full-delivery goal remains active. This checkpoint records an ordinary-worker
location matching candidate and salary rollout progress, rather than completion
of the crawler service migration.

## Salary rollout

[PR #10177](https://github.com/colophon-group/jobseek/pull/10177) was merged on
2026-09-30 at 10:11:53 UTC as `3fe58bcaf639f3b666cb283627dd1e5bb5eda80f`,
v0.13.900. Its exact checked head was
`56d80cdf724d436be35fc11b3252cbe7f6c5fe09`, against main
`9f068eb2352867bc0ed6b9835f23b7339d28c6a2`. Required CI, Crawler Deploy Gate
and installed-image parity passed. Concurrent Codex service/CLI/smoke changes
were incorporated before the bound merge.

[Deployment 36700930614](https://github.com/colophon-group/jobseek/actions/runs/36700930614)
was still running when this candidate checkpoint was prepared. Do not infer
successful promotion from the merge or an image build. Re-read the entire run
and committed release before staging selectors or activating the cohort.

The supported cold rollback retired cdom epoch 135 at epoch 136: 21 schedules
restored, two terminal tasks dropped, no write fences remaining, and the
ordinary writer set healthy. All 25 selectors were cleared with the committed
helper, under the mutation lock, against promoted revision
`20b4031ccc7e3056c6ca10b55b68bd5562c0c720` (v0.13.898).
The prior concurrent v0.13.899 deploy stopped at the active-B0 guard before
quiescing writers. No hold or receipt was bypassed.

After a successful **full** salary deployment/promotion, stage the 25 selectors
using `scripts/migration-jsonld-selectors.py` at the new full promoted revision,
then use the supported `activate cdom` wrapper. Preserve immutable image and
receipt identities, queue conservation, content hashes and natural execution
results. No forced due times, publisher refetches or duplicate origin probes.
Follow [the salary handoff](24-go-lightpanda-salary-checkpoint-2026-09-28.md)
for the guarded commands, substituting current authoritative identities.

## Location matching candidate v0.13.901

`apps/crawler/go/job-enrichment` now has a reusable SQLite-backed Go location
resolver and a bounded `location-resolver` JSONL companion. Public resolution,
display names and ancestor traversal dispatch through it when the enrichment
engine is Go. Errors propagate; there is no per-task Python fallback.

The parent loader still uses the existing PostgreSQL pool and unchanged SQL,
including 500-key non-core-name backfill chunks. It populates a private disk
index instead of retaining the complete SQLite database in Python memory.
Go opens that index read-only and keeps only per-operation entry caches.
Forks do not signal the parent's resident or delete its index; process restarts
rehydrate the same file and negative cache. Dependency notices are bundled in
the image. The native worker/executor migration must replace the Python loader
and backfill owner before Python retirement.

Evidence prepared locally:

- 322 frozen resolutions from 329 passing Python regression tests; matching
  leaf IDs, location types/order, lookup misses and raw miss samples.
- The existing 329-test suite also passed through the native public resolver.
- 248 frozen CPython integer-set cases preserve equal-context ranking ties;
  Unicode digit classes, accent/name variants and regex boundaries retain the
  existing rules.
- 368 focused Python tests passed, including six location lifecycle/ownership
  tests and real load/backfill using one pool acquisition at a time and
  500-key chunks.
- Go race, vet, module and format checks passed. Installed-image verification
  is included in the candidate CI; its result is required before rollout.
- A read-only repeatable production taxonomy snapshot contained 37,526 entries,
  143,004 core names and a 7,925,760-byte SQLite index. Go matched 1,024
  resolution cases and 128 display/ancestor cases exactly, with zero drift.
  Index SHA-256:
  `610a9cfc22654666d3a00627f916411caabc6315fbe47255ef98088a2b36648a`.

The private snapshot, inputs and replay evidence live outside Git in
`/Users/Viktor/.codex/migration-evidence/go-salary/2026-09-30/` (protected local
evidence). A future operator can recreate the replay from the read-only
production taxonomy; local paths alone are not deployment authority.
This is compatibility evidence, not a whole-lane resource comparison or a
completed location rollout.

## Remaining runtime ownership and next delivery

Ordinary workers and the browser worker select `JOB_ENRICHMENT_ENGINE=go`.
The hardened B0 DB executor overlay currently does **not** select that engine,
so its shared enrichment remains Python by default. Do not describe Go counters
from ordinary workers as evidence of an entirely native B0 lane. Its replacement
is the next major delivery boundary.

1. Finish salary deployment/promotion, supported cohort restoration, natural
   ordinary-worker salary proof and a deployed checkpoint.
2. Review and deploy the location candidate through Required CI and Crawler
   Deploy Gate, after salary promotion. Re-read live ownership first; perform
   the same supported cold transition when B0 is active. Verify natural Go
   location calls, canonical IDs/type arrays, misses/backfill and freshness.
3. Replace the B0 DB executor with Go using the existing fenced transaction and
   authorization/commit protocol. Move taxonomy/index loading and backfill onto
   its existing bounded connection budget. Prove lease loss, crash, stale epoch,
   transaction rollback and cold reversal before changing production ownership.
4. Introduce native ordinary workers for already ported HTTP/API profiles, then
   reconcile the entire enabled fleet and remaining runtime consumers. The
   current read-only effective census has 7,884 enabled boards, 102 monitor
   values, 205 effective profiles, 17 implicit scraper rows and 523 boards with
   any browser requirement. These are coverage denominators, not native-owner
   counts; the Python orchestration boundary remains.
5. Complete comparable whole-lane CPU/RAM/density/cost measurement and final
   supported cutover/reversal. Retire production Python, Playwright and Chromium
   after replacement authority and the rollback window are established. Retain
   useful isolated offline tooling. Refresh and validate the official Lightpanda
   nightly separately through its supported pinned renderer deployment.

Honor deployment holds, mutation locks and exact head/base merge authority.
Keep owner-closed #7966 closed; its retirement criteria still apply. The merged
[continuation plan](27-go-lightpanda-continuation-plan.md) remains the full goal's
sequence and completion contract.
