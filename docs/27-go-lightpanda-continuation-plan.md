# Go and Lightpanda migration delivery plan

Full delivery remains **active and incomplete**. Preserve every enabled board;
complete production Go execution, native ownership lifecycle and recovery, then
remove production Python, Playwright and Chromium. Keep useful offline Python.

## Current checkpoint — 2026-10-17

Release **0.13.1081** is merged in [PR #10665](https://github.com/colophon-group/jobseek/pull/10665)
as `2ba9c62a977307d1e8d5299ca5defbf24ee902ce`, with an identical whole tree to
qualified head `36459bec8df50c33b51ead19090ce08ba12556eb`. Complete current-head
Required CI, native runtime contracts and the actual Crawler Deploy Gate pass.
The original crawler and renderer workflows are running for that source;
**incoming native ownership is not yet granted**. Lightpanda remains **1.0.1**,
the latest stable release verified on October 17 UTC.

The outgoing **0.13.1080** cohort served **7,840 monitors / 7,859 details** and
passed all **1,771,567** captured actual scheduled routes, with zero mismatches
among queued cache records. Longer observation found **9,163** eligible records
absent from both detail lanes and the scrape cache. All lacked descriptions.
The original ordinary retirement completed at **11:58 UTC**, restoring legacy
workers, but follow-up still found **9,132** eligible unqueued records. The
supported B0 reversal subsequently completed at cold epoch **367**, with both
native receipts absent; the reviewed helper cleared all **25** selectors and
independent readback confirmed absence. No production SQL, Redis, lease or
receipt was manually patched.

[Issue #10693](https://github.com/colophon-group/jobseek/issues/10693) tracks this
queue gap. Release1081 reuses the existing **64-item** fenced publisher in both
ordinary and streamed URL-only monitor paths. Real 513-item Workday and
SmartRecruiters new/lost-enqueue repair cases pass, reducing ownership/cache
hash reads from **1,558 to 46**, with exact canonical stubs, first-time queues
and settlement retained. Focused race suites pass **31 queue / 38 worker** nodes
without fixture skips. The live causal link and sustained repair remain open.

The same release qualifies **two monitors and three detail configurations**,
preserving 7,840/7,859 and targeting **7,842/7,862**. Fresh full-cohort replay
checks all **1,771,583** proposed scheduled routes and five full original config
bindings; it reports the existing queue gap explicitly. Both monitors match
fresh Chromium 20-URL inventories over actual HTTP/1.1 and HTTP/2. Follow the
original source-bound deployment/staging/activation workflow, then verify the
exact affected IDs, fresh full-fleet conservation and subsequent large monitor
cycles. Production Python/browser removal remains gated by the full delivery
criteria below.

### Previous serving proof

The completed **0.13.1079** serving proof binds source
`18eb097289dd6deb93d0166a9c7ebb1f2c922357`, with latest stable Lightpanda **1.0.1**.
The full **7,840-monitor / 7,838-detail** cohort served at epoch **364**,
preserving every member of the previous 7,840/7,833 floor and adding five detail
boards across DOM/JSON-LD HTTP/API and unchanged direct routes.

[PR #10660](https://github.com/colophon-group/jobseek/pull/10660) delivers shared
browser capacity before claims at configured discovery/monitor concurrency 60/15.
Qualified head `83b51633dd8a473eb078e7d79c2ef860fa64bb72` and installed merge have
identical whole repository trees. Full Linux execution suites, Required CI and
Crawler Deploy Gate pass. Both original deployments and source-bound supported
staging/activation pass; independent all-ten-actor serving readback passes.
Activation completed at **23:53:10 UTC**. Full postactive replay validates all
**1,747,993 owned scheduled URLs**, with **zero owned cache mismatches**. Current
accounting resolves all three captured unqueued records and every outgoing-only
ID; none remains eligible and unqueued. This is non-atomic current evidence,
not historical or global zero-loss proof.

The next supported retirement and cold B0 reversal completed at epoch
**365**, with both native receipts absent and all 25 outgoing selectors cleared
through the original helper and independently checked. Source1080 merged as
`77e02b324f2f42a9e767f0dba03fc2d9ec12a4eb`; both original deployments and
source-bound activation later succeeded, as detailed in the current recovery
checkpoint above. The local SSH result was lost across a host sleep, so serving
was established independently before any retry; activation was not duplicated.

See the [latest portable checkpoint](37-go-lightpanda-shared-route-checkpoint-2026-10-17.md).
The [previous checkpoint](36-go-lightpanda-document-batch-checkpoint-2026-10-16.md)
retains the source1078 full route and exact-ID/current-eligibility evidence.

Freshness remains unfinished: at **23:54 UTC**, **1,638,756 owned postings** are
more than 24 hours overdue. Early successful cycles and zero restarts/OOMs do
not establish sustained freshness; measure the startup claim-error delta and
useful throughput after the full census. Outgoing actor-resource and task windows
were separate and excluded the renderer host, so matched whole-lane cost also
remains open.

[PR #10663](https://github.com/colophon-group/jobseek/pull/10663) adds ordered
HTTP-to-browser detail fallback and original empty-DOM no-fetch behavior with
full lifecycle tests. Current head `4a94d4d4f0e83b378d014b727ccc446d86261426` has
a corrected Lua parity/proxy-fixture follow-up; complete Required CI, installed
image parity and the actual deploy gate pass. Its merged whole tree equals this
qualified head. The proposed **21-detail, seven-family** batch passes fresh
source1079 full canonical/cache fingerprint and all **2,194** actual additional
scheduled route checks, preserving 7,840/7,838 and targeting
**7,840/7,859**. Nineteen existing compiled candidates had no scheduled routes
at the old capture; one uses an existing no-fetch contract and one uses the new
ordered chain. This is a qualification proposal, not an ownership or empty-inventory
claim.

[PR #10665](https://github.com/colophon-group/jobseek/pull/10665) is the next
prepared batch: three full-config scoped normal-Lightpanda replacements, two
full-config scoped HTTP monitor readers, and immutable detail membership indexing.
Six original DOM sources match all fields through compiled consumers, canonical
writes and isolated Linux execution. Both monitors match fresh original 20-URL
inventories over HTTP/1 and HTTP/2; real queue tests preserve failure and publisher
policy. Changed configs and foreign sources reject. The integrated publication fix at
`36459bec8df50c33b51ead19090ce08ba12556eb` passes complete current-head CI and
fresh full-cohort qualification. The merged source has the identical whole tree;
original deployment and supported admission remain required, as recorded above.

## Continue in substantial shared-contract batches

The captured remaining frontier is **57 monitors / 59
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

Use the [portable delivery checkpoint](37-go-lightpanda-shared-route-checkpoint-2026-10-17.md)
and its linked evidence for the detailed prior proofs. Earlier chronological
notes are preserved in [Git history](https://github.com/colophon-group/jobseek/blob/cbd1f37f3803a29dcd6693aa0a120992513edda6/docs/27-go-lightpanda-continuation-plan.md).
