# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-10-31 21:54 UTC**. The migration goal remains active.
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

[PR #11299](https://github.com/colophon-group/jobseek/pull/11299) merged and
**0.13.1141** completed the [supported full rollout and promotion](https://github.com/colophon-group/jobseek/actions/runs/41242427213).
This narrow Intervieweb/Zinrec provider fix follows the qualified 1140 release;
the comparison and production-window results below remain explicitly dated 1140.

| Identity | Value |
| --- | --- |
| Tested head | `1b0383741758e0f181778686281a40b371151ea9` |
| Runtime source | `d1a2f4243e2098daae410c10f09eae2e0144bf46` |
| Runtime contract | `616d966236554797a9f13c9b2429fa98aaeccaa80d966f59666dc1f4edf473d5` |
| Image | `sha256:86cfbb53072811f18f77ac5972af4f3d76db33004161e651622c8b5237c313ff` |

Required CI and Crawler Deploy Gate passed before the head-bound merge.
The rollout includes the retirement topology from
[PR #11080](https://github.com/colophon-group/jobseek/pull/11080): `worker-2`,
`worker-3` and `browser-1` are absent; `worker-1` is an off-profile native
maintenance carrier. No legacy processor runs. The processing image exposes no
Python, uv, Playwright or Chromium command. Native processing, enrichment, PDF
extraction, scheduling, persistence, failure handling, sync, migrations,
maintenance, currency refresh, export and description drain are implemented.

Independent readback at **21:46:36** verifies exact source/image and all four
native actors running with zero restarts/OOM
and original budgets. The complete live environment matches the committed release,
excluding only its documented `COMPOSE_FILE` override. A company-data preflight's
19:34 drift rejection occurred during the full rollout; no host repair or guard
bypass was used.

## Current fleet and downstream effects

At 21:46:36, the independently observed data revision is
`ee23f1db6b13ddb15659484386dee34b6f03fd3f`, data contract
`6a67d295e818f1e9e570c524862c75113385edc009dd65d852f7acbd6be5c309`,
with **8,192 enabled boards**, routing epoch **404**, schema **0041** and zero
active migration plans. A subsequent selected-row read at 21:54:09 observes data
revision `be11e7a79b28f7a6e455152758d9a193f397c66f`, contract
`4c14b0c2b841bc547d5ffc670fd9d23e561ea5a6bb180e8588447a15c7a6b917`.
Company publication advances independently of runtime; the earlier fleet count
is not a fresh census of that later revision.

Exact immutable CSV/SQL key equality and original-subset reconciliation at 21:15
preserve all original **8,163 board UUIDs**, plus 28 new keys at data revision
`ca27188439e4fd08f4e10001a04a42ef9692f48d`. The original sorted UUID hash
remains `73b4d3b1b4be51da0a610f158a64c7e6b71bddffc5dc1a0d9d8716bb5edc9e29`.
The 21:13 atomic Redis observation represents all **8,191** enabled monitors
at that revision exactly once: **8,177 queued + 14 valid leases**, zero missing, duplicate,
wrong-lane, expired-only, token-presence, cache/domain or deadletter gaps.
Refresh these identities after later publication rather than reusing saved counts.

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

1. **Resolve or classify Oracle ADF's observed failure.** Its natural 1139 attempt
   at 17:10 failed during fetch after about three seconds. The existing Go HTTP
   JSF route previously reproduced all 52 live jobs. Version 1140 adds bounded
   stage/status diagnostics for the next scheduled retry, November 1 at 17:10 UTC.
   Do not force work, reset backoff, replace the route wholesale or add Chromium.
   Establish whether this is a migration regression or an upstream/source failure;
   make the smallest shared fix if needed and preserve the enabled board.
2. **Close the original requirement audit after the ADF diagnosis.** Current-release
   installation, CSV/SQL/queue and original UUID checks, downstream effects,
   controlled comparisons and production stability are qualified within their
   stated limits. Keep large existing backlogs and old hash metadata explicit.
   They do not justify rewriting approved offline/control Python. New-company
   variants and renderer relocation remain separate work. Preserve qualified
   rollback assets through the affected normal schedule observation, then use
   supported retirement rather than deleting them manually.

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

All four scenarios at the tested 1140 head completed **six alternating arms**
with identical canonical output, description uploads, search results and cleanup.
The control explicitly uses Python JSON-LD/DOM extraction and enrichment; Go
reaper/exporter/drain are common measured actors.

| Mean per complete arm, native / Python | AMD64 generated 536 | AMD64 captured 72 | ARM64 generated 536 | ARM64 captured 72 |
| --- | --- | --- | --- | --- |
| Completion seconds | 19.4225 / 35.6271 | 34.0694 / 65.8402 | 19.9818 / 32.1547 | 34.0806 / 65.7034 |
| Lifetime CPU seconds | 22.1013 / 42.7822 | 18.0720 / 28.1117 | 14.1058 / 27.2471 | 16.0163 / 23.8015 |
| Sampled peak working memory, MiB | 448.8307 / 907.5091 | 423.1576 / 814.8529 | 468.4870 / 921.2435 | 416.0964 / 822.5820 |
| Retained working memory, MiB | 431.8529 / 855.3971 | 412.5013 / 799.1888 | 457.2630 / 867.1589 | 410.2305 / 803.8424 |
| Modeled EUR per 1,000 correct postings | 0.0000244480 / 0.0000495612 | 0.0001900895 / 0.0004742951 | 0.0000203493 / 0.0000463863 | 0.0001883728 / 0.0004759023 |

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
and 1139 then shipped together through the full rollout above. The cold-qualified
deploy workflow/scripts, Compose, Dockerfile and bridge contracts were unchanged
by the 1139 monitor-progress or 1140 claim-isolation changes; immutable source
comparison confirms that operational contract is unchanged. Preserve explicit rollback target
1137 source `6934e672a1e10ac5557c025af23f127197af3bbd`, image
`sha256:05aebf3cc204f9256319433d849f9b97e127fb1db31fc894cd3c7ec281144172`.

Latest official asset digests were rechecked on October 31: installed/pinned Lightpanda
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
