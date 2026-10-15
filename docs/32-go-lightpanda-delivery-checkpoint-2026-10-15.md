# Go and Lightpanda delivery checkpoint — 2026-10-15

The goal is full production migration. A merged port, deployed image or compiled
profile does not establish production ownership. Useful isolated offline Python
tools may remain; production crawling must finish moving to Go HTTP/API routes
and Lightpanda before Python, Playwright and Chromium can be removed.

## Current release and activation

PR [#10544](https://github.com/colophon-group/jobseek/pull/10544) delivered
0.13.1061, source `cf71baa66147eb825c9569bfa70be87be7e92c91`.
Exact-head Required CI, full native queue/worker suites, installed-runtime
contracts, both Linux Lightpanda architectures, independently audited16-arm/eight
complete-output ARM pairs and the actual Crawler Deploy Gate passed. The merged
and qualified trees match. Original crawler run
[38690879150](https://github.com/colophon-group/jobseek/actions/runs/38690879150)
and renderer run
[38690978972](https://github.com/colophon-group/jobseek/actions/runs/38690978972)
succeeded. Independent source/image/data/health/exporter checks passed; B0 is
active at epoch338.

Fresh admission covered7900boards and1706384scheduled postings, preserving the
full7830monitor/7783detail cohort with no held candidates. Original ordinary
staging succeeded, but activation reached the existing20-minute bound during
canonical projection at07:12:01UTC. Original recover-pending succeeded.
Independent source/image/seven-health/B0 checks verify restored serving with no
ordinary receipt, projection or active SQL plan. Ordinary Go ownership remains
inactive; production Python still serves that fleet.

The SQL stream fix removed repeated UUID pages, but every row still repeated
shared Redis TYPE and sorted-set edge checks. Candidate0.13.1062 caches only
those invariant checks within one atomic Lua invocation. Every row's identity,
source, stage, priority and lease validation remains; inspect and apply each
revalidate from a fresh cache. Original cold barriers, canonical SQL fields and
deadlines, SAVE-before-grant, runtime budgets and the overall20-minute bound stay.

The installed same-snapshot825-record comparison returns identical results. Seven
paired timings improve median inspection from0.194s to0.083s. A full read-only
rehearsal passed1647884canonical rows in1610batches with no refusals:199.299s in
Redis and274.596s including SQL/Python streaming, maximum batch0.205s. It performs
no writes, grants or origin requests and excludes Go profile validation, actual
repair effects, SAVE and SQL grant. See the
[candidate evidence](evidence/go-native-cold-queue-validation-candidate-2026-10-15.json).

The guarded pre-cutover capture preserves exact1506232queued IDs among1646979
selected scheduled postings. Its140747scheduled-versus-observed-queue gap predates
cutover. Compare future postactive IDs with current SQL/receipt accounting before
claiming conservation; do not silently enqueue absent work or attribute the gap
to this migration.

The closed batch preserves 7,830 monitors and the entire prior 7,742-detail floor,
adding 41 detail configurations for a target of 7,783. Qualification includes 51
complete-field references across 29 boards, three original empty-mapping
configurations and nine original inferred no-scrape configurations. Field,
worker and classifier sources are unchanged by the repair; reuse those references
and repeat current source/config/cache/all-route binding after deployment.

The previous source1059 census covered all 7,900 enabled boards and 1,711,527
scheduled detail postings. Its selected cohort contained 1,651,887 postings and
386 stale cached routes across 20 newly added boards. That source's activation
refused a legitimate pending fallback whose historical hash differed from a
later SQL description hash. Original recover-pending cancelled the inactive
stage and restored verified B0/base serving. Supported B0 reversal and selector
clearing subsequently established cold335 for the repair release.

Source1060 preserves valid historical fallback hashes, stages, intervals and
priority, keeping canonical SQL rows/deadlines and all source/config/lease/B0/SAVE
guards. Original versus candidate read-only inspection reproduced the rejection
on 809 of 826 actual ThirdChannel records; all four candidate pages pass with no
queue changes. Original Python R2 staging produces identical description bytes
across six historical/current hash variants. Real-store projection, ordered
adoption/retirement and compiled-worker tests pass. See
[the repair evidence](evidence/go-native-fallback-projection-hash-candidate-2026-10-15.json).

The 41-board batch spans direct DOM metadata and alternate reads, Oracle vanity
boards, JOIN empty mappings, Workday recovery/projection repair, PDF/document
details, rendered navigation waits and inferred rich-monitor no-scrape behavior.
Do not add another port batch before its supported activation and independent
serving verification.

Lightpanda [1.0.0](https://github.com/lightpanda-io/browser/releases/tag/1.0.0)
is pinned and remains the latest official stable release, rechecked October 15.

## Runtime consumers still to retire

The current [Compose contract](../apps/crawler/docker-compose.yml) still starts
three Python ordinary workers and one Python browser worker for configurations
outside native ownership. Their health probes are Go. The ordinary Go worker,
B0 claimant/executor and drain already use native executables; the independently
observed posting exporter is also native.

The forward [deployment path](../apps/crawler/deploy.sh) uses native migrations,
Typesense schema setup, registry sync and cutover repairs. Historical rollback
still invokes the pinned image's Python sync and Umantis repair. The
[exporter entrypoint](../apps/crawler/scripts/exporter-entrypoint.sh) retains its
Python-owner branch. The [crawler image](../apps/crawler/Dockerfile) still installs
Python and Chromium. These compatibility paths and image dependencies must be
removed after the actual rollback drill/window, rather than counted as already
retired because forward maintenance uses Go.

## Execution order

1. Qualify candidate1062 on its frozen head and deliver through Required CI,
   Crawler Deploy Gate and the original quiesced deployment. Use supported B0
   reversal/clearing for outgoing1061; verify the new source/images and establish
   its fresh B0 epoch. Repeat the complete source/config/cache/route census,
   stage one new ordinary plan and execute one original activation. Verify
   receipt, SQL plan, Redis projection, source/images, health, processing and
   repaired routes independently. Preserve before/after queued IDs and account
   separately for the pre-existing scheduled-versus-queued gap. Never start a
   duplicate activation because an observation times out.
2. Recompute the remaining frontier from the active cohort. The fresh source1059
   census identifies 67 monitors and 114 detail configurations outside the staged
   batch; this is planning evidence, not production admission. Of those
   details, 64 compile and 50 require additional contracts: DOM 33, JSON-LD 14,
   API sniffer two and Onlyfy one. Browser proxy, stealth, channel and persistent
   context options account for much of the unsupported set. Of the compiled
   details, 23 also have stale cached routes and 41 await complete-output
   qualification. Reuse the existing cold repair for qualified configurations.
3. Deliver grouped shared contracts across multiple configurations per release.
   Start with the eight DOM/JSON-LD configurations without active proxy or
   launch-identity controls. Four DOM configurations already pass navigation,
   action and parser checks but are rejected for fragment URLs; the renderer
   already preserves requested/final fragments. Pair that shared admission fix
   with browser-lane ordered DOM/JSON-LD fallback. Preserve the chain's browser
   queue and each stage's configured HTTP or rendered transport with fenced
   handoff. Other gaps include alternate reads, warmup and user-agent controls.
   Onlyfy language handling already exists: its retained uncompiled case points
   at a Workday board with no scheduled postings and needs configuration
   diagnosis, rather than a duplicate language implementation.
   In the same iteration, qualify existing compiled families where original
   complete outputs are available. Treat the other 41 unsupported configurations
   as shared browser-control work across DOM, JSON-LD and API capture.
   Prefer existing Go HTTP/API families or complete upstream data when they
   preserve behavior. Implement required Lightpanda controls where necessary.
   Preserve publisher policy, origin limits, complete fields and canonical writer
   effects. Upstream failures or empty originals remain explicitly classified;
   they do not establish complete successful-output equivalence. Do not silently
   ignore required controls or disable enabled boards to manufacture coverage.
4. Establish fleet freshness and conservation while the full cohort runs. Before
   this cutover, 1,598,500 scheduled postings were over 24 hours late. Track that
   SQL age distribution alongside queue/lease/receipt accounting and successful
   writes and exports. Compare whole-lane CPU, RAM and throughput over identified
   workloads. Natural unpaired samples and synthetic ARM pairs do not establish
   causal savings or attributable production cost.
5. Finish the remaining production-consumer audit, exercise supported cold
   reversal against the actual schema/data shape, and record the rollback window
   and pinned recovery artifacts. Then remove production Python execution,
   Playwright, Chromium and obsolete runtime assets together with their CI and
   image dependencies. Keep useful offline Python tools isolated.

## Operational boundaries

Use Git-deployed B0 and ordinary wrappers, mutation locks, exclusive ownership and
the [production deployment contract](adr/006-crawler-deploy-quiescence-and-rollback.md).
The ordinary native admin has a 1,200-second deadline; its wrapper allows 1,220
seconds. Callers must allow at least 35 minutes for the complete stop, operation,
readiness and containment sequence. Let leases expire naturally. Never edit
production SQL, Redis, leases or receipts manually, or replace the wrappers ad hoc.

Source1061 uses one read-only stream and at most1024-record batches before Redis
SAVE and SQL ownership grant. Candidate1062 reduces repeated invariant Redis
validation within each batch. Partial failures remain staged
and use the original recovery protocol. A successful unit test or Mac benchmark
cannot replace installed Linux verification of that operation.

The full goal remains active until enabled coverage, production freshness and
conservation, comparable resource/cost evidence, supported reversal, rollback
window and production runtime retirement are complete.
