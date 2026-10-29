# Go and Lightpanda migration delivery plan

## Current checkpoint — October 29, 2026

**Recovery is the immediate priority.** PR
[#11009](https://github.com/colophon-group/jobseek/pull/11009), runtime
**0.13.1119**, passed required checks and merged, but its full deployment and
rollback both failed the guarded expired browser-lease sweep. Independent
readback at **17:18 UTC** found all eleven processors stopped. Rollback selected
prior source `9d6760016279f7b4263133694ac768daefaa18ed`, image
`sha256:a0d6912593fae767e7b0e74ade30c9bc8d61b18905bb4977f9d24d237b33f259`,
with default ownership at epoch **404**. Do not infer runtime health from an
earlier successful workflow or a selected image.

The complete migration goal remains active. Use **self-hosted Lightpanda or
proven Go HTTP/API routes**, preserve every enabled board, and keep the existing
resource limits. The latest full workflow is
[40917608147](https://github.com/colophon-group/jobseek/actions/runs/40917608147).

The installed renderer is **1.1.0-nightly.11275+78bbb64db**, upstream commit
`78bbb64dbe1a3e1df8d2299cbf05efcff81bd65f`, matching the latest official
October 29 Linux assets. Its immutable image is
`ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:4361f406e77fc0bd2fab56533e0bab51b56773e2c398cdbd78fd1e7b7c707805`.
Retain the reviewed 1 GiB memory, zero swap, concurrency, child heap and response
limits. Check upstream again when continuing on a later release day.

## Recovery batch — 0.13.1120

Read-only inspection of the first 200 expired browser entries found three
completed current-epoch API-sniffer monitor receipts with exact current tokens,
stable effective configurations and unchanged canonical deadlines. Their
canonical and cached route is Go HTTP, while the expired attempt remains in the
browser lane. The previous reaper refuses that lane mismatch, blocking the batch.

The recovery retains all token, epoch, configuration, domain, deadline and
ownership checks. Only an exact default-mode completed receipt can retire its
stale lane and restore the canonical successful deadline in the compiled lane.
An existing target queue, first-time queue, live attempt or dead-letter
representation is preserved. All affected indexes and deadlines are checked
before any batch effects. Both lanes retain the original rate and rotation
floors. No fetch or canonical database write is replayed.

The original full workflow drains expired attempts before registry sync in both
forward deployment and rollback. Sync then removes obsolete queued monitor
lanes; a conflicting live attempt still fails closed even without a queued
duplicate. No manual SQL/Redis/env repair, partial restart, epoch adjustment,
timer extension or resource increase is authorized by this recovery.

Actual PostgreSQL/Redis regressions pass for completed receipt recovery,
wrong-lane retirement, preserved target schedules/attempts/dispositions, unchanged
canonical data, idempotence and atomic refusal. Offline replay confirms all three
captured receipts retain their exact effective hashes, domains and deadlines and
compile to Go HTTP. Complete required CI and the original full deployment before
claiming recovery.

The previously exercised complete cold reversal succeeded in workflow
[40899557014](https://github.com/colophon-group/jobseek/actions/runs/40899557014).
Retain the supported reversal contract in
[ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable identities,
mutation locks, full writer quiescence and authenticated generation/spec
restoration. A failed restore must keep writers stopped.

## Remaining acceptance work

The complete installed-source verifier checked **2,038,879 scheduled detail
routes** and found zero cache identity mismatches, but refused 14 stale monitor
lanes across API sniffer, TalentBrew, DOM and sitemap. The 1119 sync correction
covers those four families. Production acceptance has not passed.

Fifteen active scheduled details on former B0 boards lack their cache and queue
projection despite existing canonical deadlines. A missing-only recovery
extension is prepared separately for validation after processing is restored.
Preserve existing schedules, fallback steps and canonical deadlines; the global
rebuild is unsuitable because it rewrites existing runtime state. A sixteenth
snapshot gap is now canonically unscheduled.

The complete queue boundary contains **3,465,138 identities**. All 290 removals
from the previous boundary are accounted for: 275 inactive/disabled and 15
unscheduled. This conservation result does not resolve the fifteen missing
active schedules above.

The completed native resource window had stable process identities. Compared
with the legacy natural window, aggregate CPU was **44% lower**, working memory
**7% lower**, useful successful tasks **29% lower**, and successful tasks per CPU
second **27% higher**. Modeled whole-lane compute per 1,000 successful tasks was
**12% higher**. These are consecutive natural windows with different task mixes
and shared storage work, not randomized matched jobs or billed savings. Repeat
acceptance after the routing correction; do not claim a throughput or cost win.

## Native runtime contract

The `native` image contains Go ordinary workers, Typesense exporter, R2 drain,
lease recovery, registry/migration/maintenance and ATS inventory commands, plus
conversion/OCR dependencies and enrichment models. It contains no Python, uv,
Playwright or Chromium runtime. The production workflow builds this target once
under both immutable crawler release repository identities.

Default mode serves the full enabled catalogue using the installed executable's
source, the explicit allocator epoch and the existing SQL/configuration/token/
lease fences. It rejects mixed-owner migration authority. Registry publication
validates both configured execution paths inside its canonical transaction.
Unsupported configurations must fail before publication.

The four processing services are `ordinary-go`, `lease-reaper`, `exporter` and
`drain`, alongside Redis and Alloy. The native reaper uses the existing SQL
receipt and Redis CAS protocol. Forward deployment and complete rollback drain
expired attempts after quiescence and before provider route repair; they retain
compatible live attempts and reject unproved lane changes, orphan tokens or duplicates.

Additive schema **0041** records a default monitor's effective configuration
hash separately from its runtime snapshot. Receipt recovery permits mutable
egress metadata while retaining configuration, token, epoch, canonical deadline
and ownership guards. A changed runtime snapshot cannot replay a stale learned
host. Existing retained runtimes keep their explicit-column SQL ABI.

## Finish the production exit checks

1. Complete recovery candidate 1120 required checks and its original full deployment; bind the selected source,
   actual image digest, data generation, default mode and allocator epoch.
   Check all four native actors, exporter/drain/worker health and stopped legacy
   processors. Investigate an unexpected epoch; never adjust it manually.
2. Verify actual canonical/cache configurations for **all 8,142 enabled boards**
   and every scheduled active detail URL with the installed source's SDK.
   Validate identities, domains, queues, fallback steps and intervals. Account
   for every non-atomic snapshot gap with a current canonical disposition.
3. Compare complete unfiltered queue/inflight identities with the retained current
   boundary. Explain every removal. Check real native receipts, canonical fields,
   enrichment, exporter progress and publisher/failure behavior. Unavailable or
   reserved publishers must preserve inventory rather than become empty results.
4. Verify natural whole-fleet monitor/detail freshness and useful completions over
   sustained intervals. Do not clear circuits, force jobs, reset counters or
   change timers/budgets to manufacture acceptance.
5. Compare matching resource windows for the complete crawler and renderer nodes,
   PostgreSQL and Typesense, including the reaper, Redis, exporter and drain.
   The legacy control completed ten samples per node: **726 monitor / 1,454
   scrape successes in 566.8 seconds**. Report task mix, CPU, working memory,
   density and regional compute attribution. Identify shared storage workloads;
   resource-share models are not billed savings, and unchanged provisioned nodes
   retain their current compute charges.
6. Establish the supported retained recovery window, then retire production
   Python/Playwright/Chromium execution and obsolete runtime-only assets. Keep
   only recovery material required by the deployment contract. Useful isolated
   Python company setup, labelling, research, tests and CI/operator tooling may
   remain where reasonable; do not port them merely to remove a language.
7. Update this checkpoint with the accepted installed identities, results,
   limitations and remaining work. Mark the migration complete only when the
   required production outcomes are demonstrated.

## Storage and continuation

Keep one current checkpoint, one current conservation boundary, current
qualification and operational rollback inputs. Delete superseded local captures,
archives, copied scripts, test databases and compiled test binaries after each
completed batch. Historical evidence need not be preserved. Do not change
production generation retention or remove other contributors' work.

Earlier chronology is available in Git history; it is not the current serving
state or acceptance authority. Continue from the exact live workflow and current
independent observations instead of replaying historical cutover commands.
