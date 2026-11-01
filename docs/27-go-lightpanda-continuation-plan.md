# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-11-01 21:25 UTC**. The migration goal remains active.
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

**0.13.1149** completed the [supported full rollout and promotion](https://github.com/colophon-group/jobseek/actions/runs/41342411111)
after [PR #11468](https://github.com/colophon-group/jobseek/pull/11468).
Required CI, Crawler Deploy Gate, both physical architectures, all four controlled
comparisons and the advisory B0 job passed on the exact tested head. The full
quiesced rollout completed at **19:55:41 UTC**; no duplicate/manual deployment,
environment repair or partial writer restart was used.

| Identity | Value |
| --- | --- |
| Tested 1149 PR head | `9af885fcac5c859e5f6589462d47da8e5338a277` |
| Runtime source | `a515fb91381a0f836ac5f8ea690fc100cabb9839` |
| Runtime contract | `69c84d7230f9bf0d4ad0d012caa3c0f2853cece83547010da2d8fafd03b485ea` |
| Image | `sha256:bacc6e3714270501899cbd3e2658426bf4c2578b59f390e2b8d28bdeea2a9189` |
| Data revision | `a515fb91381a0f836ac5f8ea690fc100cabb9839` |
| Data contract | `c3e9d4473edae6e82589cf78c4a8c8982d513e92c98429d7637832e4d437e1fa` |

Independent **20:11:42 UTC** readback verifies all four native actors, exact
source/image/VERSION, zero restarts/OOM and the installed ordinary worker limits:
**4 CPU / 2 GiB**, **1 GiB Go memory target**, **120 discovery / 30 monitor slots**.
Reaper remains 128 MiB and exporter/drain 256 MiB. All six core services are
running, with configured health checks passing. Complete live environment
(except documented `COMPOSE_FILE`), active v3 pointer, manifest, success/env hashes
and all eight actual CSV hashes match. No legacy processor runs; the processing
image exposes no Python, uv, Playwright or Chromium command. Native processing,
enrichment, PDF extraction, scheduling, persistence, failure handling, sync,
migrations, maintenance, currency refresh, export and description drain are implemented.

The release integrates the exact PDF test correction from draft
[PR #11462](https://github.com/colophon-group/jobseek/pull/11462), attributed to
source `004a34a850fe2f8591076620850da48f0ee5cd16`, and removes stale aggregate
registry-count assertions while preserving row, provider, transport, enrichment
and negative checks. Neither change relaxes production route admission.

## Current fleet and downstream effects

The same readback has **8,270 enabled boards**, routing epoch **404**, schema
**0042** and zero active migration plans. Company intake may advance independently;
refresh installed identities before reusing these counts after publication.

All **8,270** pre-capacity board UUIDs and keys survive the cutover unchanged:
sorted UUID SHA-256 `0d5cd7219ce3a5d7ddff2b7b7948a06a8a028a068cb0730aedd5e396acf927e1`;
key SHA-256 `651cbd58dd32ca986b477e1da315c57773ec8d1174833ef602c4e39db066a287`.
Atomic Redis readback represents **8,245 queued + 25 valid leases = 8,270**,
with zero missing, duplicate, wrong-lane, expired-only, cache/domain, token-presence
or deadletter gaps. Token values are never read. Earlier original-set proof
preserves all 8,163 original UUIDs (SHA-256
`73b4d3b1b4be51da0a610f158a64c7e6b71bddffc5dc1a0d9d8716bb5edc9e29`).

The downstream and production-window samples in the following paragraphs are
explicitly **October 31** observations, not fresh 1149 samples.

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

### Capacity delivered; freshness remains under observation

The authorized increase is installed. The four-host normal-traffic baseline
**19:31–19:43 UTC** used the previous 2-CPU/1-GiB worker; the post window is
**19:58–20:11 UTC** (784.62 seconds on the crawler). Both use two endpoint samples
per host. All four native actor generations remain stable with no restart or OOM
increment. Search stayed healthy (200); PostgreSQL and renderer generations also
remain stable. One unclassified crawler container present at the start is absent
at the end, so this window does not establish a complete whole-lane container CPU
integral. Host CPU and the common processing actors remain observable.

| Observation | Previous allocation | Installed allocation |
| --- | --- | --- |
| Ordinary worker mean CPU cores | 1.492 | 2.358 |
| Crawler host mean busy cores / 8 | 2.555 | 3.630 |
| Successful monitors / minute | 102.03 | 144.45 |
| Successful details / minute | 222.09 | 91.15 |
| Worker sampled working RAM, start → end | 189 → 181 MiB | 137 → 205 MiB |

These are different natural workloads, not equivalent-workload speedups or billed
cost savings. Post-window outcomes include **1,889 monitor / 1,192 detail
successes**, five monitor / 99 detail failures, 40 monitor / 267 detail circuit
deferrals and 15 claim errors. Exclude 577 skips from success throughput;
421,256 posting touches are not unique completed jobs. This does not establish
error-free operation or that the freshness backlog is cleared.

Post-window minimum sampled available memory is **11.43 GiB crawler**, **5.46 GiB
PostgreSQL**, **2.09 GiB search**, **3.09 GiB renderer**. Their mean busy cores are
3.63/8, 1.18/4, 0.60/4 and 0.27/2 respectively. Search had an independent OOM at
18:16 before capacity deployment, recovered naturally by 18:39, and passed private
production active/year facets and seven aliases at 18:43. Its six-GiB contract
remains unchanged; post samples show no new OOM/restart. Continue observing its
memory pressure under existing issue #8033 rather than increasing it implicitly.

At 20:11 the regular detail queues contain **3,169,250 simple / 62,359 browser due
entries**, including **2,842,544 / 29,721** overdue a week. Redis entries are not a
census of unique currently eligible scheduled postings. The earlier 17:25 SQL
census counted 1,667,322 eligible details due. Outstanding demand persists; the
worker is below its new CPU ceiling and sampled active claims are 64 then 44.
Further CPU allocation alone is not yet justified as a detail-throughput remedy.
Inspect scheduling mix, per-host eligibility and downstream pressure before another
increase; preserve publisher throttles and four browser execution slots.

Justup's backend coordinator confirmed the separate ARM renderer host allocation:
reserve **0.5 CPU / 1 GiB**, zero swap, its already allocated two-GiB app filesystem
and an **eight-GiB root free-space floor**. Post-window root free minimum is
**9.7905 GiB**; do not subtract the preallocated filesystem twice. Renderer remains
one CPU / one GiB with unchanged image and generation. No Justup/renderer rename,
restart or relocation was part of this release. The temporary main/data and
provider-renaming holds were explicitly released after the post-window checks;
future runtime work requires fresh coordination. The naming implementer reserved
0.13.1150; this checkpoint changes documentation only.


A later **20:11–21:03 UTC** four-node window covers about 52 minutes with the same
observed native generations and no restart/OOM increment. It records 5,492 monitor
and 8,648 detail successes; ordinary worker mean CPU is 1.904 of four cores,
sampled working RAM 205 → 620 MiB within two GiB. The observed-container mean CPU
sum is 4.02 cores and final working RAM 11.94 GiB. Endpoint membership does not
exclude transient containers; host accounting includes their potential work.
At 21:05 all **8,273** monitors are represented (8,249 queued / 24 valid leases),
with zero observed gaps. At 21:08 every original **8,270** key/UUID is independently
preserved with unchanged original UUID hash and exactly three additions.
The two authority-loss outcomes classify as `cache_snapshot` fencing refusals in
the complete bounded log window; unrelated work continues and no panic marker is
observed. Search remains healthy and the demo reservation/root floor hold. Retain
the failure, circuit, claim-error and skip counts and limits in the
[steady-traffic report](evidence/go-native1149-steady-2026-11-01.json).
This mixed workload does not establish a causal speedup or cleared freshness
backlog, and does not qualify a further CPU allocation increase.

1. **Resolve Oracle ADF's observed failure.** Its normal retry ran November 1 at
   17:37:14 UTC, without forced work or a backoff reset. The installed Go route
   rejected **HTTP 302 during pagination**; failures rose to 12 and its next
   normal retry is November 2 at 17:37:17 UTC. Readback at 17:58 verifies it remains
   enabled, unreserved, quarantined and correctly represented in its browser
   monitor queue, with no active host circuit or rate limit. This identifies the
   failing stage, but does not prove a universal upstream failure or a safe
   redirect target. The earlier exact 52-job Go HTTP result was a local candidate
   qualification. A separate [1102 serving checkpoint](evidence/go-native1102-serving-conservation-2026-10-27.json)
   explicitly records native ADF completing **52 jobs / zero failures** at October
   27 15:27 UTC, after supported Go activation. That witness was recorded in
   `16904e793eb29532cd20ca7e2565b691ab86e91f`; its active source and unique original
   registry assignment are reconciled with current configuration. The audit had
   missed this positive production record. It establishes dated native coverage,
   not recovery of the current failure or its cause. Retain complete-inventory
   checks and publisher policy; do not blindly follow the redirect, force work,
   reset backoff or add Chromium. The operator authorized bounded local
   investigation. Fresh Go HTTP controls still return pagination 302 when using
   the advertised form action or standard AJAX headers; neither is a proven fix.
   Fresh document/view-state and no-cache controls do not support a stale-bootstrap
   explanation in the observed samples. The official publisher careers link
   exactly matches the registry entry point. Stabilized browser instrumentation
   shows the form page ID differs from the ADS header, and its URL window ID is
   initially empty. Reproducing that shape with full successful controls still
   returns 302. An exact publisher-generated request submitted once through Go
   with all six browser cookies also returns a zero-body 302 to the site root;
   the next same-origin GET redirects away from the application. No cross-origin
   redirect was followed by that Go diagnostic.

   Setting the window ID through the publisher framework aligns URL and form
   values, but still does not resolve Go submission or a native Lightpanda More
   action. The latter retains 25 rows, fetch index 24 and More; it is not a
   completed inventory. Earlier unmodified More controls also preserve the exact
   bounded row-identity hash. These checks use no detail scan and make no
   production queue, counter, backoff or schedule changes. No speculative runtime
   patch is qualified. Local Go diagnostic TLS/egress and the local macOS browser
   are separately scoped from installed Linux runtime qualification.

   The original endpoint/configuration and functional Go request logic match the
   earlier successful local candidate; installed 1149 retains the compared ADF
   and direct-transport files unchanged. This is bounded evidence against a new
   request-logic regression, not proof of a universal upstream outage or its cause.
   The [current protocol evidence](evidence/go-native1149-adf-protocol-2026-11-01.json)
   and [open follow-up](https://github.com/colophon-group/jobseek/issues/10790#issuecomment-6563361945)
   record the remaining boundary. Caller reconciliation between successful
   native 1102, release 1109 at quarantine and installed 1149 finds the same ADF
   dispatcher, direct HTTP transport and Accept implementation. The 1102-to-1109
   runtime changes propagate the renderer argument and select HTTP/2 for another
   provider; ADF remains on verified direct HTTP/1. No ADF-specific caller or
   request-code change was found through quarantine. This source comparison does
   not establish historical per-attempt egress or session behavior. Those actual
   environment observations, a working publisher-supported complete source, or a
   relevant upstream correction would provide new discriminating input. Preserve
   the current freshness gap in final acceptance.
   Do not repeat rejected header/form/window variants without new evidence,
   accept partial inventory or reset the normal retry.

2. **Close the original acceptance audit after that outcome.** Native 1149 and
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

The exact tested 1149 head `9af885fcac5c859e5f6589462d47da8e5338a277`
passed [all four standard comparisons](https://github.com/colophon-group/jobseek/actions/runs/41338441359):
six alternating arms per scenario, identical canonical output, nonempty uploaded
descriptions, matching search output and successful cleanup. All nine actors,
including their startup/lifecycle CPU, are counted. The separate
[ARM64 B0 job](https://github.com/colophon-group/jobseek/actions/runs/41338441250)
also succeeded. Exact per-arm resources, source/runtime/workload/canonical hashes,
image identities and sanitized installed/post-window observations are in the
[current numeric report](evidence/go-native1149-comparison-2026-11-01.json).
The older 1148 report is historical and does not qualify the scaled allocation.

| Mean per complete arm, native / Python | AMD64 generated 536 | AMD64 captured 72 | ARM64 generated 536 | ARM64 captured 72 |
| --- | --- | --- | --- | --- |
| Completion seconds | 18.6680 / 32.1919 | 35.5490 / 67.6437 | 19.7846 / 32.1372 | 33.8847 / 65.6893 |
| Lifetime CPU seconds | 15.7912 / 31.1391 | 24.1272 / 36.7495 | 14.4017 / 28.4574 | 16.3360 / 24.2471 |
| Sampled peak working RAM, MiB | 448.4883 / 915.2760 | 426.3060 / 819.7865 | 462.2513 / 965.4701 | 428.1250 / 822.2344 |
| Retained working RAM, MiB | 439.7461 / 858.1836 | 416.0117 / 797.0104 | 443.7956 / 910.4414 | 420.7630 / 803.5221 |
| Correct postings / lifetime CPU-second | 33.9432 / 17.2136 | 2.9846 / 1.9593 | 37.2181 / 18.8508 | 4.4077 / 2.9698 |
| Modeled EUR / 1,000 correct postings | 0.0000192195 / 0.0000457223 | 0.0002249110 / 0.0005076937 | 0.0000198459 / 0.0000486540 | 0.0001899085 / 0.0004766186 |

The controlled fixture ordinary worker remains **one GiB**; these comparisons
qualify its code and canonical behavior, while the separate installed/live proof
above qualifies production's 4-CPU/2-GiB allocation and 120/30 slots. The Python
control performs JSON-LD/DOM extraction and enrichment; Go reaper/exporter/drain
are common actors. Captured inputs contain decoded HTML for 62 JSON-LD and ten
DOM details from seven hosts and three recorded configurations, not original
headers, rendered dependencies or network timing. Generated work covers discovery
and rendered paths. Preserve the compressed regression corpus; remove consumed
private captures and owned temporary binaries/caches. Both arms use the same
fixture-only TLS exception; this is not production TLS-cost evidence.

Each scenario uses one physical architecture, rather than production's cross-host
mixed architecture. Sampled RAM peaks are lower bounds; retained RAM is separately
reported. Modeled cost is not billed or universal fleet savings. Production
mixed-work observations retain their failure/skip counts and do not substitute
for these equivalent-work fixtures.

## Cold reversal, Lightpanda and operational boundaries

The [retirement cold exercise](https://github.com/colophon-group/jobseek/actions/runs/41213487268)
finished successfully before normal cutover. Independent readback verified exact
previous 1137 source/image/data, all 14 deployment-spec presence/hash fingerprints,
8,163 UUIDs/schedules, native actors and budgets. Promotion was skipped. A separate
1138 normal run was safely cancelled before a deployment job existed; retirement
and 1139 then shipped together through the full rollout above. The cold-qualified deployment workflow/scripts, Compose, Dockerfile and bridge
contracts retain the same reversal workflow through 1149; worker resource limits
change in 1149. Schema 0042 is an independently owned
additive source-coverage change; the earlier cold exercise is not a new 1149
reversal measurement. Rollback sync uses the authenticated previous image and
complete previous CSV generation without rerunning its migration command. Preserve explicit rollback target
1137 source `6934e672a1e10ac5557c025af23f127197af3bbd`, image
`sha256:05aebf3cc204f9256319433d849f9b97e127fb1db31fc894cd3c7ec281144172`.

The immediate supported pre-capacity release is native 1148, source
`6b19a2ce3508e78f996624c4243f574eda3d7cae`, contract
`b2f536065a07221f034b63e77c4ba38a849974a8d1d75faa041e420af9d66eb9`, image
`sha256:5f4796e02c73e8157eeeb5191796021866ec6190e7e7246f3c4cea196f394bf6`,
with last committed data revision `3840f8d98d02242afb15fbc254cf81f475702384`
and data contract `c3e9d4473edae6e82589cf78c4a8c8982d513e92c98429d7637832e4d437e1fa`.
Preserve its authenticated v3 rollback generation; do not combine newer CSVs with
an older runtime spec. The earlier cold exercise is dated proof, not a fresh
1149-to-1148 reversal. Use the full supported workflow if reversal is necessary.

Latest official assets were rechecked **November 1 at 20:13 UTC**:
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
comparisons subsequently passed; the current 1149 B0 performance job also passed.
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
