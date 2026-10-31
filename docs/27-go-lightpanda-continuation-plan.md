# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-10-31 17:35 UTC**. The migration goal remains active.
Use current installed identities and canonical registry state before saved counts.
Keep one current checkpoint, active regression inputs and supported rollback assets;
historical raw evidence need not be preserved. Preserve unrelated work and `dump.rdb`.

## Delivery scope

**Operator decision:** only performance-sensitive paths need Go. Keep host
firewall/containment, provisioning, deployment verification and low-frequency
control tools in Python. Useful isolated workspace, labeller, test-oracle and
offline tools may also stay. Their language does not block completion. Do not
expand this migration into a host security or control-tool rewrite.

The required native scope is crawler execution, extraction/enrichment,
queue/scheduling/persistence, publishing/export and other measured hot paths.
Preserve every enabled board, canonical output, database effects, publisher
policy, schedule conservation and supported rollback. Production transport stays
**latest self-hosted Lightpanda or proven Go HTTP/API; no Chromium fallback**.

## Deployed release and retirement

[PR #11191](https://github.com/colophon-group/jobseek/pull/11191) merged and
**0.13.1139** completed the [supported full rollout and promotion](https://github.com/colophon-group/jobseek/actions/runs/41221115395).

| Identity | Value |
| --- | --- |
| Tested head | `0ee97d4a5c8131bc3f7fbf54f6f96923634e8686` |
| Runtime source | `28dfc0e887c4579bc5d01005770538e87eec56c1` |
| Runtime contract | `584baf7707a3e545b3c87f5f41e4c1fce1afb6d683d3fdf4276e41cba5d839a6` |
| Image | `sha256:e22d09cf2a42087590ada9911515274a7ed6b71e94957a256c2641ce7155bdee` |

Required CI and Crawler Deploy Gate passed before the head-bound merge.
The rollout includes the retirement topology from
[PR #11080](https://github.com/colophon-group/jobseek/pull/11080): `worker-2`,
`worker-3` and `browser-1` are absent; `worker-1` is an off-profile native
maintenance carrier. No legacy processor runs. The processing image exposes no
Python, uv, Playwright or Chromium command. Native processing, enrichment, PDF
extraction, scheduling, persistence, failure handling, sync, migrations,
maintenance, currency refresh, export and description drain are implemented.

Readback at **17:34** verifies the exact source/image, all four native actors
running, original budgets and no OOM. Ordinary worker restarted once at
**17:17:27**; the reaper, exporter and drain retain their 16:58:20 generation.
Do not reuse the earlier zero-restart observation as current stability proof.

## Current fleet and downstream effects

At 17:34, the independently observed data revision is
`81fc1038886b48cb841d91ab349eb2cbe277fa5e`, data contract
`908fafdd2acffc58efc3e87ec1b560ff69eee63df0b52d93e32548d2548be4bb`,
with **8,170 enabled boards**, routing epoch **404**, schema **0041** and zero
active migration plans. Company publication can advance independently of runtime.

The earlier atomic reconciliation at 17:02 represented all **8,168** then-enabled
monitors exactly once: **8,152 queued + 16 valid leases**, zero missing,
duplicate, wrong-lane, expired-only, token-presence, cache/domain or deadletter
representation gaps. Independent CSV/SQL equality and original-subset checks
preserved all original **8,163 board identities**, including UUID hash
`73b4d3b1b4be51da0a610f158a64c7e6b71bddffc5dc1a0d9d8716bb5edc9e29`.
The fresh 17:35 atomic observation represents all **8,170** currently enabled
monitors exactly once: **8,155 queued + 15 valid leases**, with the same zero-gap
checks. Refresh CSV/SQL equality and original UUID preservation after later
publication rather than reusing the older membership hash.

At 17:34, 4,825 monitors succeeded and 5,059 were checked in the preceding hour;
2,520 were due and none overdue a day. The pre-existing detail backlog remains
large: at 17:02 about 1.84 million were due and 1.76 million overdue a day.
These bounded observations do not establish universal freshness.

At 17:02, current-generation samples covered 716 successful details across 32
provider groups with zero missing titles. Of 716 nonnull posting hashes, 715
matched uploaded SQL rows; one asynchronous upload remained pending. Ten mature
Typesense samples matched canonical title/company. At 17:12, actual R2 GET bytes
matched SQL UTF-8 HTML SHA-256 for **10/10 descriptions**, five provider groups,
HTML content type and **63,310 bytes**. Endpoint/bucket fingerprints matched the
installed drain. This does not prove every object was uploaded by this generation.

## Remaining delivery work

1. **Explain the worker restart before qualifying stability.** The 17:09–17:18
   resource reducer correctly rejected a changed worker generation. Bounded
   exit evidence shows `settlement:authority_lost` at 17:17:26, followed by task
   cancellation and command rejection. There was no panic or OOM. The runtime
   intentionally exits on authority loss; determine whether this was claim-local
   fencing or worker-wide authority loss before changing behavior. Preserve all
   write/token/epoch fences. Re-run a stable production window after resolution.
2. **Resolve or classify Oracle ADF's observed failure.** Its natural 1139 attempt
   at 17:10 failed during fetch after about three seconds. The existing Go HTTP
   JSF route previously reproduced all 52 live jobs; do not replace it wholesale
   or infer that Lightpanda caused the current failure. Obtain a discriminating
   failure stage/status or reproducible input, then make the smallest shared fix.
   Next scheduled retry is November 1 at 17:10 UTC; do not force an attempt.
3. **Observe the remaining long-running monitor case.** Accenture, Beisen and both
   tracked API-sniffer routes recovered naturally on 1137. Amazon's old attempt
   still hit the fixed ten-minute deadline; its next normal retry is 18:50 UTC.
   At 17:12 no completed 1139 monitor had yet exceeded ten minutes. Do not claim
   natural long-cycle recovery from the scaled integration tests alone.
4. **Finish current-release acceptance.** Refresh CSV/SQL/queue membership,
   canonical effects, publishing/search and applicable freshness after any fix.
   Preserve qualified reversal assets and report remaining external/provider
   failures and backlog honestly. Approved host/control/offline Python is not
   outstanding work. Keep new-company provider variants in their own issues.

## Shared monitor progress fix

1139 renews the existing ten-minute default inactivity budget only after
successful authoritative batch writes and detail publication. Heartbeats and
upstream responses cannot renew it. Explicit operator absolute task caps,
detail deadlines, the Njoyn provider cap, lease TTLs, heartbeat intervals,
cancellation grace and capacity limits remain unchanged. Parent cancellation
cannot grant fresh settlement authority.

Verification: 21 focused unit and real PostgreSQL/Redis race cases pass. The
complete local module reports 4,246 passes and 35 skips; six DOC cases initially
failed because a temporary local extractor lacked character maps. All 38 affected
document cases passed with that fixture corrected, without production source
changes. `go vet`, required CI and the native race/integration jobs passed.

## Comparable efficiency and limits

All four scenarios at the tested 1139 head completed **six alternating arms**
with identical canonical output, description uploads, search results and cleanup.
The control explicitly uses Python JSON-LD/DOM extraction and enrichment; Go
reaper/exporter/drain are common measured actors.

| Mean per complete arm, native / Python | AMD64 generated 536 | AMD64 captured 72 | ARM64 generated 536 | ARM64 captured 72 |
| --- | --- | --- | --- | --- |
| Completion seconds | 19.5856 / 35.2506 | 34.0462 / 64.1052 | 20.0541 / 30.1874 | 33.9688 / 64.0830 |
| Lifetime CPU seconds | 21.3424 / 41.5439 | 14.7498 / 22.6670 | 13.9218 / 26.9280 | 15.1307 / 22.5123 |
| Sampled peak working memory, MiB | 452.4063 / 912.1484 | 427.1914 / 820.1628 | 462.1211 / 938.1224 | 420.9427 / 822.2188 |
| Retained working memory, MiB | 435.4180 / 858.2552 | 414.5078 / 800.3711 | 447.5456 / 881.9557 | 412.5495 / 801.5560 |
| Modeled EUR per 1,000 correct postings | 0.0000237244 / 0.0000491248 | 0.0001737230 / 0.0004658808 | 0.0000198899 / 0.0000458077 | 0.0001836670 / 0.0004673997 |

Captured inputs contain exact decoded HTML for 62 JSON-LD and ten DOM details
from seven hosts and three recorded configurations. Both parsers reproduce saved
fields. Keep the compressed regression corpus; consumed private captures and
temporary binaries are removed. Configuration provenance is time-bound. Both arms
use the same fixture-only TLS exception; no production TLS-cost claim follows.
Captured scheduled details exclude discovery, rendered dependencies, original
headers/network timing and live provider mix. Generated work covers discovery
and rendered paths. All nine actors are counted; startup CPU is included in
lifetime CPU but separate from modeled window cost. Each run uses one physical
architecture, not production's cross-host mixed architecture. Sampled peaks are
not absolute peaks; modeled cost is not billed or universal fleet savings.

The stable pre-batch 1137 production window at 16:41–16:50 covered 831 successful
monitors and 867 successful details, with 361 skips excluded, over 539.26 seconds.
Whole-lane means were 3.18 CPU cores and 10.86 GiB working memory. Seven monitors
and 76 details failed. This actual mixed-work accounting is not equivalent-work
Python evidence. The final 1139 window is **unqualified** because of the restart.

## Cold reversal, Lightpanda and operational boundaries

The [retirement cold exercise](https://github.com/colophon-group/jobseek/actions/runs/41213487268)
finished successfully before normal cutover. Independent readback verified exact
previous 1137 source/image/data, all 14 deployment-spec presence/hash fingerprints,
8,163 UUIDs/schedules, native actors and budgets. Promotion was skipped. A separate
1138 normal run was safely cancelled before a deployment job existed; retirement
and 1139 then shipped together through the full rollout above. The cold-qualified
deploy workflow/scripts, Compose, Dockerfile and bridge contracts were unchanged
by the seven-file monitor-progress change. Preserve explicit rollback target
1137 source `6934e672a1e10ac5557c025af23f127197af3bbd`, image
`sha256:05aebf3cc204f9256319433d849f9b97e127fb1db31fc894cd3c7ec281144172`.

Latest official assets were rechecked at 16:18: installed/pinned Lightpanda
**1.1.0-nightly.11423+56980afde**, executable source
`56980afdec3ad177072e7a180cb05da838bc1744`; stable 1.0.2 is older.
[Official nightly release](https://github.com/lightpanda-io/browser/releases/tag/nightly).
ARM64 SHA-256 `97710997faea70bb0f22f9b07ba3a9701b47dbb6a71f679150b040e70c565cf3`;
AMD64 SHA-256 `19f3fee25fb14bb75a1e0c1e0f8ab9cce842950544e4a144fbad3884c94b98a3`.
Renderer source `406d0f94ee919962da2ccb4196450344265c9445`, image
`sha256:c09939c34ba034ee9d68ec9176f19f8ddeea04bfa816ce595409b09386133af7`.
Recheck official identities before changing it. Host sampler source remains
`cad060906a8fc11b4036ee69ac0a81e26d2a26fb`; do not confuse it with runtime source.

No renderer relocation has occurred. Relocation is a separate change: the default
worker endpoint is compiled into `rendered_detail.go`, and the renderer workflow
currently builds ARM64 only. An x86 target needs explicit architecture support,
new protected endpoint/PKI/pins, scoped containment, supported activation and
rollback. Available host headroom alone does not qualify a move.

Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable
images, complete writer quiescence, mutation locks and authenticated restoration.
No live rsync, mutable deploy tags, partial writer restarts, forced provider work,
manual SQL/Redis/env repair, counter/deadline reset or capacity increase.
Preserve current renderer/crawler budgets and presence-only Redis token reads.
Never read, search or edit `profile_self_identifying_dom.go`.
Before merge re-read state/draft, exact head/base and actual main, checks,
reviews/comments, rules and deployment holds; bind normal merge to that head.
Retain active generation, journal references, explicit rollback target, five
newest verified v3 generations and 48-hour staged-work grace.
