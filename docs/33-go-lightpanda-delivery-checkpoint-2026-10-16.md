# Go and Lightpanda delivery checkpoint — 2026-10-16

## Installed and serving

Release **0.13.1074**, source `52f6c637a4bb11da0009fde1cd185560cffa75e8`,
merged through [PR #10610](https://github.com/colophon-group/jobseek/pull/10610).
Required CI, the actual Crawler Deploy Gate, runtime contracts, physical browser
qualification and all sixteen ARM workload arms passed on the frozen candidate.
The original [crawler deployment](https://github.com/colophon-group/jobseek/actions/runs/38846041386)
and [renderer deployment](https://github.com/colophon-group/jobseek/actions/runs/38846179020)
succeeded on their first attempts. Independent cold checks match installed source,
immutable images, data, native executable and six healthy base writers. A bounded exporter snapshot before native activation records 3,212 successful
documents, zero errors and zero unknown writers.

B0 and ordinary Go are independently active at epoch **354**. The serving cohort is:
**7,833 monitors / 7,825 details**, preserving the entire previous 7,833/7,820 floor
and adding five unchanged Phenom/Workday detail configurations through Go HTTP.
Eight positive original/native references match every raw field; archived and
current same-body parser checks, real PostgreSQL/Redis canonical writers and
all 878 captured source-route bindings pass. Original Browser queue ownership
and configuration fingerprints remain authoritative. Changed configurations,
origins or paths retain their rendered route. See the
[candidate evidence](evidence/go-native1074-http-batch-candidate-2026-10-16.json).

The original cold activation completed successfully. Independent source/image/data,
active receipt, SQL and Redis checks establish exactly one active 7,833/7,825 plan;
all ten actors run the expected images and all eight health endpoints pass.
Full postactive replay derives the identical cohort, accepts every actual source
route and finds **zero owned cache mismatches**, after a preflight observation of
137 owned mismatches. Legacy leases expired naturally during the cold window.

Installed [Lightpanda 1.0.1](https://github.com/lightpanda-io/browser/releases/tag/1.0.1)
is still the official latest stable release, rechecked October 16. The installed
renderer matches its published immutable image; the pinned executable and real
ARM/AMD64 browser qualification use the official release checksums.

## Queue accounting and operational limits

Protected exact-ID captures compare **1,715,712** outgoing queued IDs against
**1,716,986** owned scheduled postings, with **1,715,486** incoming queued IDs
against **1,715,488** owned scheduled postings. There are **1,710,263** retained
IDs, **5,223** incoming-only IDs and **5,449** outgoing-only IDs. Independent
canonical accounting classifies every outgoing-only ID: **5,398 inactive,
51 unscheduled, zero still eligible**. The two incoming capture gaps map uniquely
through their captured source routes to one inactive and one unscheduled posting;
neither is currently eligible. This resolves current eligibility for every
observed absence. The raw incoming capture gap is **two**, and the outgoing gap
was **1,274**; these non-atomic live observations do not prove atomic historical,
global or causal conservation.

Ordinary Go has restarted **three times** by 04:49 UTC. The new diagnostic identifies
`detail_render_actions` in `dom.rendered-detail/v1`. A currently owned unchanged
configuration reproduces the exact failure before any renderer/origin request:
admission decodes action numbers as `float64`, while the opaque profile copy
retains `json.Number`; action parsing rejects that numeric representation.
The 0.13.1075 candidate accepts retained JSON numbers with the same validation
limits. Action-decoding and actual opaque-profile regression tests fail before
the fix and pass afterward; all twelve current owned configurations with numeric
actions retain identical executable semantics after the fix. **The fix is not deployed and production stability
is not proved.** Earlier 1073 fatal logs lack this inner-stage evidence.

A 40-second natural sample records six successful and one failed Go monitor
cycles, 21 successful detail cycles, three skipped details and no failed details.
Ten crawler cgroups average 3.6018 CPU cores / 3.722 GB memory with zero OOM-kill
increment. Redis and the renderer are excluded, and the workload is unpaired:
this is **not comparable whole-lane cost evidence**. The initial exporter records
272 successful documents, zero errors and zero unknown writers. Initial SQL
observation finds 24 completed monitor receipts and 83 detail receipts, but still
**1,607,002** global scheduled postings overdue by more than 24 hours. Receipt
completion alone does not prove successful content or adequate freshness.

## Combined 1075 candidate

The combined **0.13.1075** candidate adds seven detail configurations across four
profile families while fixing numeric action decoding. Six configurations have
matching complete empty original/native monitor inventories and independently
verified zero scheduled detail work. The ADP case has an identical 20-URL monitor
inventory and two field-exact HTTP references. Both original ADP samples lack a
title, so their primary lifecycle is the original transient-failure path; actual
PostgreSQL/Redis replay matches the frozen original SQL, including fields, lease
and Browser deadline. No successful ADP content write is claimed. Its 20 current
active unscheduled postings stay preserved.

The proposed complete floor is **7,833 monitors / 7,832 details**. Fourteen other
screened configurations remain held; original fetch errors are not empty proof.
Exact-head CI, physical/ARM qualification, supported deployment and fresh serving
admission remain required. See [candidate evidence](evidence/go-native1075-actions-multitype-candidate-2026-10-16.json).

## Continue to full migration

1. Deliver the proven retained-action-number fix through exact-head CI, the
   actual Deploy Gate, physical/ARM qualification and supported full deployment.
   Preserve the entire 7,833/7,825 floor and verify the actual opaque-profile path.
2. Observe sustained runtime/content/export progress after that fix. Keep source,
   images, receipts, SQL, Redis, routes/cache and exact-ID accounting current.
3. Continue substantial batches across multiple types. The fresh frontier is
   **64 monitor / 72 detail configurations**: 19/37 already have compiled profiles,
   and 45/35 need supported profiles or other contract work. API sniffer, Onlyfy,
   DOM and JSON-LD remain in the grouped qualification inventory. Require positive
   original/native content or independently justified empty/lifecycle evidence,
   actual source routes and canonical writer verification.
4. Complete native-to-native deployment, CSV and rollback handoff through the
   original workflows. Prove active owner handoff and the recovery-window drill;
   retain receipt guards, mutation locks, full writer quiescence and immutable images.
5. Establish sustained freshness, exact queue/canonical accounting and matched
   whole-lane CPU/RAM/density/attributable cost, including Redis and the renderer.
   Exercise supported cold reversal and the recovery window.
6. Retire production Python execution, Playwright, Chromium and runtime-only
   legacy assets after all replacements and recovery evidence are complete.
   Preserve useful isolated offline Python and every enabled board.

The full migration goal remains **active and incomplete**. This checkpoint
supersedes the 1073 serving state. Portable identities and aggregate evidence are
in [the delivery evidence](evidence/go-native1074-delivery-2026-10-16.json).
Private IDs, raw logs, credentials and operational inputs stay outside the repository.
