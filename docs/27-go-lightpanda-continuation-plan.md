# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-11-01 15:58 UTC**. The migration goal remains active.
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

**0.13.1148** completed the [supported full rollout and promotion](https://github.com/colophon-group/jobseek/actions/runs/41316335984)
after [PR #11442](https://github.com/colophon-group/jobseek/pull/11442).
This independently owned release adds native source-coverage observations and schema **0042**.
The preceding migration fixes in [PR #11440](https://github.com/colophon-group/jobseek/pull/11440)
read comparison postings, descriptions and completed-board state from one read-only
repeatable-read snapshot and remove a stale provider-total assertion while checking
every registry row and all required provider families.

| Identity | Value |
| --- | --- |
| Tested 1148 PR head | `cb42323b80e6fc474359c61134db40429de65e11` |
| Runtime source | `6b19a2ce3508e78f996624c4243f574eda3d7cae` |
| Runtime contract | `b2f536065a07221f034b63e77c4ba38a849974a8d1d75faa041e420af9d66eb9` |
| Image | `sha256:5f4796e02c73e8157eeeb5191796021866ec6190e7e7246f3c4cea196f394bf6` |

Required CI and Crawler Deploy Gate passed before merge. The actual merged source
also contains the latest Lightpanda pin from PR #11438. The final renderer probe
fix in PR #11450 has its own exact-head comparisons below; native runtime
contract `b2f536...` is unchanged across these qualified sources.
The rollout includes the retirement topology from
[PR #11080](https://github.com/colophon-group/jobseek/pull/11080): `worker-2`,
`worker-3` and `browser-1` are absent; `worker-1` is an off-profile native
maintenance carrier. No legacy processor runs. The processing image exposes no
Python, uv, Playwright or Chromium command. Native processing, enrichment, PDF
extraction, scheduling, persistence, failure handling, sync, migrations,
maintenance, currency refresh, export and description drain are implemented.

Independent readback at **15:58:36 UTC** verifies exact source/image/VERSION,
all four native actors running with zero restarts/OOM, and existing limits:
ordinary worker 2 CPU / 1 GiB, 512 MiB Go heap, discovery 60 / monitor 15;
reaper 128 MiB and exporter/drain 256 MiB. The full live environment matches the
committed release except its documented `COMPOSE_FILE` override. The active v3
pointer, manifest, Compose/env/success hashes and complete eight-file CSV tree
are independently checked. All six core services are running; configured Docker
health checks pass. Processing uses native commands with retired runtime commands absent.

## Current fleet and downstream effects

The 15:58 observation has **8,228 enabled boards**, routing epoch **404**,
schema **0042**, zero active migration plans, data revision
`9b027872ded9259e074ccc48d499ecf1788b24e4` and data contract
`5d6456d55db83f5c5ffe4410d63eff51362c8f3782a6de52761b853e70411aac`.
Company data can advance independently; refresh its installed identity before using
these counts after subsequent intake publication.

The **15:57:55** readback preserves every original **8,163 board UUID** and all
**8,218 pre-cutover UUIDs**. Original sorted UUID SHA-256 remains
`73b4d3b1b4be51da0a610f158a64c7e6b71bddffc5dc1a0d9d8716bb5edc9e29`;
all-pre-cutover UUID SHA-256 is
`51d44c751f289fd4d4900f3160a77e45a0fbbc378f2b7de35eb70c6a7309f216`.
Atomic Redis observation represents **8,216 queued + 12 valid leases = 8,228**,
with zero observed missing, duplicate, wrong-lane, expired-only, token-presence,
cache/domain or deadletter gaps. Token values are never read.

The downstream and production-window samples in the following paragraphs are
explicitly **October 31** observations, not fresh 1148 samples.

At 19:49, 5,175 monitors succeeded and 5,426 were checked in the preceding hour;
2,030 were due and none overdue a day. The pre-existing detail backlog remains
large: at 19:47 about **1.82 million** were due, **1.74 million** overdue a day
and **1.51 million** overdue a week. These observations do not establish universal
freshness. The successful current-started detail count in the preceding hour is
5,090; 6,333 eligible scheduled details have never been scraped.

At 19:47, current-generation samples cover **1,000 successful details across 31
provider groups**, with zero missing titles. They include **232 browser-configured
details across five groups**, also with zero missing titles. Of 1,000 posting
hashes, **999** match uploaded staged rows. All 1,003 staged rows in this sample are
nonempty and uploaded. The unmatched row retains staged metadata dated April 7;
both its staged and scalar hashes differ from its exact HTML byte hash. Do not
claim universal hash convergence or attribute this historical state to the new
worker without evidence. A separate actual GET of that old object also matches its
SQL HTML bytes (**5,856 bytes**); this is a metadata gap, not observed content loss.
Ten mature Typesense records match canonical title/company.
Actual R2 GET bytes match SQL UTF-8 HTML SHA-256 for **10/10 descriptions**, five
provider groups, HTML content type and **49,548 bytes**. Endpoint/bucket
fingerprints match the installed drain. Objects can predate this generation.

The original named required-title browser case is now qualified through the
existing native BambooHR monitor and HTTP `api_sniffer` detail route.
[PR #11346](https://github.com/colophon-group/jobseek/pull/11346) changed one
registry row and its census/plan, passed required checks, and completed the
[automatic data publication](https://github.com/colophon-group/jobseek/actions/runs/41241750984).
The board UUID and source scope are preserved. Standard provider-type sync
cleared the old attempt state and scheduled native validation; no manual reset
occurred. Do not claim preservation of the old counters/deadline or count the
preceding DOM success as proof of the new route.

The new monitor naturally succeeded at **21:25:00**, leaving the board active,
zero failures and zero empty cycles. At 21:54, its 41 stored postings include
**two active jobs**: both have titles, locations, descriptions staged and marked
uploaded, and detail completions after the API monitor. Both have zero scrape
failures. This closes the observed empty-inventory/missing-title requirement for
current active output; it does not repair historical inactive records or claim
a new independent R2 byte comparison.

A separate company-intake location correction is also installed: both active
postings have the correct stored taxonomy, Frankfurt/Munich in Germany and Bern
in Switzerland. The unsafe CSV attempt failed at the pre-mutation runtime-drift
guard; the full 1141 rollout's data-revision anchors retain `ca271884...`, and
the later corrected publication succeeded. There is no observed unsafe
publication; current corrected state alone is not exhaustive historical proof.
Shared source-quality bugs reproduced by both Python and Go remain bounded
company-intake work, rather than new migration completion requirements.

## Remaining delivery work

### Requested capacity increase — candidate 0.13.1149

The operator authorized increasing the Go crawler where measured headroom can
serve outstanding demand, while preserving Justup's demo reservation. The
17:25 UTC readback has 1,667,322 eligible details due, including 1,584,678 overdue
a day. The preceding nine-minute normal-traffic window covered all four hosts:
the crawler host has eight CPUs, averaged 2.88 busy cores, and retained about
11.3 GiB available memory. The Go worker averaged 1.51 cores and peaked at
432 MiB working memory under its existing two-CPU/one-GiB limit.

Candidate 1149 gives the ordinary Go worker four CPUs, two GiB memory, a one-GiB
Go memory target, 120 discovery slots and 30 monitor slots. Per-host throttles,
connection budgets and the four browser execution slots remain unchanged.
This is a bounded initial increase; installation, throughput, queue age and
downstream pressure must be checked after the supported rollout before claiming
that demand is met.

Justup's backend coordinator confirmed that its preview allocation is on the
separate ARM renderer host: reserve 0.5 CPU, one GiB memory, zero swap, its two-GiB
app filesystem and an eight-GiB root free-space floor. The renderer stays at one
CPU/one GiB. This candidate changes only the crawler host allocation; it does
not rename, relocate or restart Justup or renderer units. Cross-project hosting
coordination and fresh required gates remain prerequisites for rollout.

1. **Resolve Oracle ADF's observed failure.** Its normal retry ran November 1 at
   17:37:14 UTC, without forced work or a backoff reset. The installed Go route
   rejected **HTTP 302 during pagination**; failures rose to 12 and its next
   normal retry is November 2 at 17:37:17 UTC. Readback at 17:58 verifies it remains
   enabled, unreserved, quarantined and correctly represented in its browser
   monitor queue, with no active host circuit or rate limit. This identifies the
   failing stage, but does not prove a universal upstream failure or a safe
   redirect target. The earlier exact 52-job Go HTTP result was a local candidate
   qualification, not a production success witness. Retain complete-inventory
   checks and publisher policy; do not blindly follow the redirect, force work,
   reset backoff or add Chromium.
2. **Close the original acceptance audit after that outcome.** Native 1148 and
   the latest renderer are installed and independently verified. All four final
   comparison scenarios and the separate automatic B0 performance job passed. Preserve original boards, downstream/canonical/
   publisher-policy contracts and supported reversal. Keep dated observations,
   existing backlog and sampling limits explicit. Approved control/offline Python,
   new-company variants and renderer relocation do not block this migration.
   Preserve rollback assets through the affected normal schedule observation;
   retire them through supported GC rather than manual deletion.

All five tracked formerly deadlettered monitor cases are active with zero
consecutive failures after natural 1139 successes: Accenture, Beisen, both
API-sniffer routes and Amazon. Amazon's 19:11:44–19:23:05 cycle completed in
**680.49 seconds**, beyond the former ten-minute wall-clock limit. Its completed
fence and recurring queue score agree with the canonical 20:23:05 next deadline,
with no retained lease/token. No forced attempt or reset was used. This proves
normal long-cycle settlement within existing provider bounds, not uncapped global
Amazon inventory. Keep all five on their normal schedules.

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

## Shared claim-fencing and ADF follow-up

**0.13.1140** keeps rejecting stale cache snapshots, including stale
writes and acknowledgement, but lets unrelated work continue instead of stopping
the entire worker. Subsequent claims still revalidate epoch/ownership/config.
Global/uncategorized authority loss, lease/fence/receipt failures, invalid
configuration and unsafe cancellation remain fatal. Fixed code-owned reasons
identify epoch, cache, lease, fence, receipt or acknowledgement refusal without
exposing cache values, identifiers, tokens or SQL messages.

Oracle ADF retains bootstrap/session/listing/pagination/seed/detail-scan failure
stage and bounded HTTP/transport categories instead of collapsing them all to an
incomplete-inventory error. Existing GET backoff, single-attempt POST, session,
resource binding, inventory completion and publisher-reservation checks remain.
This supplies evidence for the next normal attempt; it does not claim recovery
of the observed provider failure or change transport.

Focused real PostgreSQL 18/Redis race checks and both-module vet pass, including
committed-receipt/lease retention and concurrent-work isolation. Required CI,
Crawler Deploy Gate, both native PostgreSQL 17 suites and all four controlled
comparison scenarios passed on tested head
`38e5dfe0927213a5bfc3e803cfb4747377aa777b` before the normal head-bound merge of
[PR #11271](https://github.com/colophon-group/jobseek/pull/11271). Two registry
tests now validate every current row and positive family coverage without freezing
totals that change when valid companies are added. Production acceptance remains
separate from these checks.

## Comparable efficiency and limits

The final renderer-fix head `6b08c345647d06bbd9b23edf33e3304840a55649`
passed [all four comparisons](https://github.com/colophon-group/jobseek/actions/runs/41321036234):
**six alternating arms per scenario**, identical canonical output, uploaded
nonempty descriptions, matching search output and successful cleanup. All nine
actors are included. Native runtime contract is the installed 1148 contract;
renderer code matches the reviewed merged source. The control uses Python
JSON-LD/DOM extraction and enrichment; Go reaper/exporter/drain are common actors.
Exact per-arm resources, source/runtime/workload/canonical hashes and image
identities and sanitized production readbacks are in the
[numeric report](evidence/go-native1148-comparison-2026-11-01.json).
The earlier combined-main run 41318736961 also passed all 24 arms before this
probe-only fix; it is not substituted for the final-head results.

| Mean per complete arm, native / Python | AMD64 generated 536 | AMD64 captured 72 | ARM64 generated 536 | ARM64 captured 72 |
| --- | --- | --- | --- | --- |
| Completion seconds | 20.3703 / 31.0156 | 33.9659 / 63.9735 | 19.2645 / 32.0488 | 33.9824 / 63.9868 |
| Lifetime CPU seconds | 12.6979 / 26.3188 | 14.8628 / 23.6123 | 14.6503 / 28.2546 | 15.5594 / 23.2734 |
| Sampled peak working memory, MiB | 453.2839 / 923.6536 | 424.2734 / 824.2331 | 461.9948 / 927.1719 | 428.3242 / 819.1523 |
| Retained working memory, MiB | 439.0312 / 851.7969 | 413.3984 / 799.6185 | 446.9414 / 870.8451 | 416.3490 / 802.1276 |
| Correct postings per lifetime CPU-second | 42.2245 / 20.5640 | 4.8479 / 3.0544 | 36.5864 / 18.9705 | 4.6275 / 3.0937 |
| Modeled EUR per 1,000 correct postings | 0.0000197692 / 0.0000447642 | 0.0001748611 / 0.0004662582 | 0.0000196852 / 0.0000465327 | 0.0001862729 / 0.0004664098 |

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

The final 1140 production window at **19:37:45–19:46:45** qualifies ten samples
per node, fixed processing generations, zero restart increment and zero OOM over
539.95 seconds. It includes 588 successful monitors and 1,023 successful details,
with 545 skips excluded. Whole-lane means are **3.48 CPU cores and 11.35 GiB
working memory**. Five monitors and 101 details failed; there were 30 monitor and
237 detail circuit deferrals and 14 claim errors. Bounded current-started logs
classify errors as ordinary failures or unacknowledged work, with no authority-loss
or panic diagnostic observed. This is not an error-free fleet claim.

A concurrent automatic company-data publication completed through its supported
workflow without changing the four processing generations. Background crawler
containers are counted separately; host accounting includes their work within
its stated limits. Crawler host minima are **11.14 GiB available memory and
33.92 GiB root disk**. These actual mixed-work counts are not equivalent-work or
billed-savings evidence. Consumed raw samples were removed after reduction.

The prior 1139 worker restart at 17:17 followed `settlement:authority_lost`; its
completed fence matched stable configuration and canonical due time while the full
cache snapshot differed. The original 17:09–17:18 resource window was correctly
rejected. The shared 1140 isolation fix preserves all stale-write/ACK fences; its
regression checks and current stable window replace the earlier pending follow-up.

## Cold reversal, Lightpanda and operational boundaries

The [retirement cold exercise](https://github.com/colophon-group/jobseek/actions/runs/41213487268)
finished successfully before normal cutover. Independent readback verified exact
previous 1137 source/image/data, all 14 deployment-spec presence/hash fingerprints,
8,163 UUIDs/schedules, native actors and budgets. Promotion was skipped. A separate
1138 normal run was safely cancelled before a deployment job existed; retirement
and 1139 then shipped together through the full rollout above. The cold-qualified deployment workflow/scripts, Compose, Dockerfile and bridge
contracts remain unchanged through 1148. Schema 0042 is an independently owned
additive source-coverage change; the earlier cold exercise is not a new 1148
reversal measurement. Rollback sync uses the authenticated previous image and
complete previous CSV generation without rerunning its migration command. Preserve explicit rollback target
1137 source `6934e672a1e10ac5557c025af23f127197af3bbd`, image
`sha256:05aebf3cc204f9256319433d849f9b97e127fb1db31fc894cd3c7ec281144172`.

Latest official assets were rechecked **November 1**:
**1.1.0-nightly.11471+cebb8d51e**, upstream source
`cebb8d51e60a68815591a5c7874823b988530aa8`,
[official nightly release](https://github.com/lightpanda-io/browser/releases/tag/nightly).
Immutable OCI index
`sha256:9a5f29a6d0d36e6d166a736e2f83e96f923fbc68e7569a9cc80270785039ef50`;
ARM64 executable SHA-256
`dfbb4a2d32867db31e6ab3ee74545e7cd5daeb847b94e0ae29a70b4732e960d4`,
AMD64 executable SHA-256
`49c84433d35ae81a94daebf696d48c2c3095f9d78d6f953c383e390eb6e5c76f`.
[PR #11438](https://github.com/colophon-group/jobseek/pull/11438) passed all 53 checks
(39 success / 14 skip), physical Linux qualification on both architectures, ARM
renderer service smoke, bounded B0 measurement and four standard comparisons
at exact head `82acf8f1c35296ef80dd96685d3fd54de88b1c47`, then merged as
`2eac9b486b058be8d3d6d6e9bb50abbc1e1585ae`.

[PR #11450](https://github.com/colophon-group/jobseek/pull/11450) adds a
three-second connection-refused startup wait and fixed diagnostic categories;
TLS success still requires the exact `certificate_required` rejection. It passed
Required CI, Crawler Deploy Gate, both physical Linux architecture tests and the
ARM renderer container smoke before a fresh expected-head-bound merge. All four
comparisons subsequently passed; the separate B0 performance job remains live.
No mTLS, resource, containment or execution-capacity guard was relaxed.

The [supported recovery](https://github.com/colophon-group/jobseek/actions/runs/41323014298)
completed successfully on renderer source
`9c40b4418728d4b9693469725cc8fd7bf4021112`, image
`sha256:61e9daa52dc0c955ad270440e962eb321d894c84c8e3ac455b2f2bfb19ab224b`.
Independent **15:54:06 UTC** readback verifies the actual latest ARM64 binary
against the official digest above, source/image labels, active marker, immutable
verifier bytes, unchanged public certificates, sole two network endpoints and
one TLS listener. The kernel records that listener in its TCP6 table; the
observer checks both tables. The controller is running with **zero restarts/OOM**,
1 CPU, 1 GiB memory, zero swap, PIDs 64 and a private cgroup namespace; actual kernel
limits match. Current start time is **15:49:39.782878988 UTC**.

The first activation at 14:04 failed predecessor cgroup attestation and contained
the old service. A cold recovery failed its silent no-client probe and removed
the candidate. The current recovery restored serving availability at 15:49.
The earlier memory drift and exact suppressed probe reason remain unproved;
success is not a retrospective diagnosis or a claim of uninterrupted service.
The connection-refused startup race is reproduced by regression tests. No forced
origin work, backoff reset, manual host repair or capacity increase was used.

No renderer relocation has occurred. Relocation is a separate change: the default
worker endpoint is compiled into `rendered_detail.go`, and the renderer workflow
currently builds ARM64 only. An x86 target needs explicit architecture support,
new protected endpoint/PKI/pins, scoped containment, supported activation and
rollback. Available host headroom alone does not qualify a move.

Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable
images, complete writer quiescence, mutation locks and authenticated restoration.
No live rsync, mutable deploy tags, partial writer restarts, forced provider work,
manual SQL/Redis/env repair or manual counter/deadline resets. The operator has
authorized the bounded ordinary-Go capacity increase described above, subject to
measured headroom, Justup demo reservations and the supported rollout. Preserve
renderer, per-host, database and browser-slot budgets and presence-only Redis
token reads.
Never read, search or edit `profile_self_identifying_dom.go`.
Before merge re-read state/draft, exact head/base and actual main, checks,
reviews/comments, rules and deployment holds; bind normal merge to that head.
Retain active generation, journal references, explicit rollback target, five
newest verified v3 generations and 48-hour staged-work grace.
