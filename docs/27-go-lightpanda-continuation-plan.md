# Go and Lightpanda migration delivery plan

## Current checkpoint — October 29, 2026

Runtime **0.13.1118** is qualified and merged through PR
[#11007](https://github.com/colophon-group/jobseek/pull/11007). The complete
migration goal remains active until the production exit checks below pass.
Execution must use **self-hosted Lightpanda or proven Go HTTP/API routes**.
Preserve every enabled board; do not add a Chromium fallback.

The installed renderer is **1.1.0-nightly.11275+78bbb64db**, upstream commit
`78bbb64dbe1a3e1df8d2299cbf05efcff81bd65f`, matching the latest official
October 29 Linux assets. Its immutable image is
`ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:4361f406e77fc0bd2fab56533e0bab51b56773e2c398cdbd78fd1e7b7c707805`.
Retain the reviewed 1 GiB memory, zero swap, concurrency, child heap and response
limits. Check upstream again when continuing on a later release day.

## Complete deployment and reversal

The original full workflow's cold rollback exercise
[40899557014](https://github.com/colophon-group/jobseek/actions/runs/40899557014)
passed. Candidate source
`9d6760016279f7b4263133694ac768daefaa18ed` reached default Go readiness at
epoch **403**, with all four native actors on the exact immutable candidate
image and all legacy processors stopped. The same full deployment transaction
then restored the complete previous release. Image promotion was skipped.

Independent readback at **15:28:10 UTC** verifies the prior source
`1a7c8cfbedcaee0c70c1a6717b23c31724a9839b`, its exact crawler/browser images,
committed data generation and original deploy specifications, seven running
legacy actors, six healthy HTTP endpoints, stopped candidate Go/reaper actors,
and no active ownership plan, receipt, selector or Redis ownership projection.
The allocator remains at **403**; it was not rewound.

The final full deployment
[40903648972](https://github.com/colophon-group/jobseek/actions/runs/40903648972)
was dispatched after complete legacy control samples and fresh main/hold checks.
It uses the same qualified source. Its independently observed build digest is
`sha256:a0d6912593fae767e7b0e74ade30c9bc8d61b18905bb4977f9d24d237b33f259`.
The full deployment and promotion passed. Independent readback verifies
source/image/data, default mode at **epoch 404**, schema 0041, all **8,142 enabled
boards**, four native actors, three healthy endpoints and stopped legacy
processors. The installed worker has no Python, uv, Playwright or Chromium
commands. Final migration acceptance remains incomplete: the worker restarted
with a configuration error, and later readback counted seven restarts.

Use the original full workflow and [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md)
for cutover or reversal. Preserve immutable source/image/data identities,
mutation locks, complete writer quiescence and authenticated generation/spec
restoration. Do not use partial restarts, manual queue/environment/epoch repairs,
mutable image tags or stale-main deployments. A failed restore must leave writers
stopped until the original guarded recovery succeeds.

## Current correction — 0.13.1119

The complete installed-source verifier read **2,038,879 scheduled detail routes**,
with zero cache identity mismatches, then refused **14 wrong-lane monitor
observations**. Sixteen non-atomic detail snapshot gaps still require current
canonical dispositions. Every enabled monitor/detail configuration and scheduled
URL reached the existing SDK checks; the complete verifier did not pass because
of the monitor lanes.

The 14 monitors retain browser-lane entries alongside their valid HTTP-lane
entries: **11 API sniffer, one TalentBrew, one DOM and one sitemap**. Registry sync
had enqueued the selected lane and updated its cache without retiring old entries
in the opposite lane. Those entries can reach the worker's existing configuration
guard. Confirm the observed restart class disappears after the correction;
do not weaken that guard or infer stability from one healthy container.

Candidate 1119 adds guarded opposite-lane cleanup to the original registry sync.
It preserves the selected deadline/tier, canonical cache and live attempt, and
rebuilds the affected ready indexes with their existing rate/rotation floors.
Conflicting inflight attempts, orphan tokens, missing selected authority and
corrupt state refuse before cleanup effects. Mixed-owner migration routing stays
with its existing owner. No new production repair command or manual mutation
is needed. CPU limits, concurrency, timers and budgets are unchanged.

The registry-sync module tests and focused Redis safety cases pass. A private
replay of all **14 actual configurations across the four families** preserves
canonical caches and selected deadlines, removes the stale entries and is
idempotent. It performs no network fetch or production mutation. Complete required
CI, the exact-source original full deployment and fresh fleet/stability checks
before accepting the correction.

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
compatible live attempts and reject changed lanes, orphan tokens or duplicates.

Additive schema **0041** records a default monitor's effective configuration
hash separately from its runtime snapshot. Receipt recovery permits mutable
egress metadata while retaining configuration, token, epoch, canonical deadline
and ownership guards. A changed runtime snapshot cannot replay a stale learned
host. Existing retained runtimes keep their explicit-column SQL ABI.

Required CI and Crawler Deploy Gate passed on exact PR head
`bea788c9e661fc7859f62584e979dc0ee7c51094`. Qualification includes 14,265 crawler
tests, native worker/queue and installed image checks, actual PostgreSQL/Redis
recovery cases, and exact native/Alembic schema parity through 0041. This is
qualification, not a substitute for the production checks below.

## Finish the production exit checks

1. Complete candidate 1119 required checks and its original full deployment; bind the selected source,
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
