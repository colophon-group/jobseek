# Go and Lightpanda migration delivery plan

Full delivery remains **active and incomplete**. Preserve every enabled board;
complete production Go execution, native ownership lifecycle and recovery, then
remove production Python, Playwright and Chromium. Keep useful offline Python.

## Current checkpoint — 2026-10-16

**0.13.1078** independently serves source
`8bfbf7d6ec425e311c10b8827d7e9b6682d3b6bb`, with latest stable Lightpanda **1.0.1**.
The full **7,840-monitor / 7,833-detail** cohort is active at epoch **362**,
preserving the previous 7,838/7,832 floor and admitting two DOM monitors plus one
browser-API detail. Original retirement/reversal, both complete deployments and
new source-bound staging/activation passed. All ten actors and health agree;
the bounded 21:37 UTC worker observation has zero restarts, OOMs or classified
fatal configuration failures.

The shared 4,000,000-byte document batch is delivered across DOM, JSON-LD,
embedded-data and inline consumers. Complete postactive compiled replay validates
all **1,746,877 owned scheduled URLs**, all queued, with **zero owned cache
mismatches**. Two prior posting-cache defects were repaired by the original cold
projection path. All 1,008 outgoing-only queued IDs are currently 893 inactive /
115 unscheduled; none remains eligible and unqueued. These are exact-ID/current
observations across non-atomic captures, not historical loss or causal proof.

See the [latest portable checkpoint](36-go-lightpanda-document-batch-checkpoint-2026-10-16.md)
and [aggregate evidence](evidence/go-native1078-serving-checkpoint-2026-10-16.json).
The [previous checkpoint](35-go-lightpanda-runtime-checkpoint-2026-10-16.md)
retains the source1077 publisher-race fix and queue evidence.

Freshness remains unfinished: at 21:36 UTC, **1,632,388 owned postings** are more
than 24 hours overdue. Matching worker snapshots show 56 successful monitor and
280 successful detail cycles in about 257 seconds without claim errors or restarts.
Actual configured discovery/monitor concurrency is 60/15. Bounded progress and
isolated ARM density do not establish sustained freshness or matched fleet cost.

[PR #10660](https://github.com/colophon-group/jobseek/pull/10660) bounds browser
claims before renderer waiters can exhaust discovery slots. Its deterministic
regression and repeated race checks pass at default 20 and configured 60 capacity;
the full Linux worker suite passes and remaining CI is pending. It is undeployed.
Continue the following JSON-LD/DOM HTTP/API batch: a production-timezone-matched
JSON-LD reference and private canonical writer agree, while Aura still needs its
separate compensation paragraph preserved. No following ownership is admitted.

## Continue in substantial shared-contract batches

The captured remaining frontier is **57 monitors / 64
details**, excluding the three B0 records. Counts require a fresh census if data
changes. Preserve the admitted floor through every subsequent batch.

Prioritize shared browser transport and configuration controls across DOM,
JSON-LD, API-browser, inline and Njoyn. The provider implementations already exist;
unsupported configuration does not imply that another provider port is needed.
An offline diagnostic that removed transport controls exposed existing parsers
for 18 more monitors and 29 details. Those altered configurations are
**unqualified**. Prove original semantics and actual content/lifecycle behavior
before accepting any replacement transport or browser-option change.

[Lightpanda supports proxy configuration](https://lightpanda.io/docs/guides/configure-a-proxy),
while the production renderer currently has no admitted proxy path. Current
unsupported configs include 19 monitor and 26 detail proxy requests, overlapping
other controls. Test a shared native transport against the full original config,
with bounded attempts, existing publisher policy, egress isolation and scoped
credentials. These occurrence counts are opportunities, not promised admissions.

Draft [PR #10643](https://github.com/colophon-group/jobseek/pull/10643) implements
shared configured browser-proxy transport across several profile families.
Required CI, architecture and renderer rollback suites and all 16 isolated ARM
whole-lane arms pass. Its deploy gate is intentionally red while draft. Proxy
ownership remains held until upstream destination enforcement is qualified;
local DNS checks do not pin a remote proxy resolver. Repeated live evidence also
includes HTTP403 and changing inventories, and the two compared detail outputs
have no positive descriptions. Complete these proofs before deployment/admission.

Prioritize proved native HTTP/API alternatives for content that Lightpanda does
not render completely, alongside shared controls across multiple profile families.
Keep the serving release and its qualification intact while developing the next
batch in an isolated worktree. Reuse unchanged compiled profiles when possible;
ownership expansion still needs supported incarnation rotation and fresh full-cohort
admission. Audit actual production commands: reconciliation already invokes Go
directly and current registry sync uses its native Go entrypoint. Preserve old
rollback-image compatibility until that recovery window expires.
Use actual original/native inventory, fields and canonical lifecycle evidence;
original fetch errors do not establish an empty inventory. Resolve rendering,
pagination and source-identity differences without dropping fields or members.

## Full-delivery exit criteria

- Every enabled monitor/detail route has a proved Go replacement and current
  canonical write/queue/freshness behavior; no enabled-board coverage reduction.
- Deployment, CSV publication, native ownership handoff, maintenance and rollback
  work through supported committed workflows with exact source/image identity.
- Comparable whole-lane CPU/RAM/density and attributable cost evidence includes
  all relevant actors and uses matched work with explicit measurement limits.
- Supported cold reversal and the recovery window are proved before deleting
  production Python/Playwright/Chromium and their runtime-only assets.

Use the [portable delivery checkpoint](36-go-lightpanda-document-batch-checkpoint-2026-10-16.md)
and its linked evidence for the detailed prior proofs. Earlier chronological
notes are preserved in [Git history](https://github.com/colophon-group/jobseek/blob/cbd1f37f3803a29dcd6693aa0a120992513edda6/docs/27-go-lightpanda-continuation-plan.md).
