# Go and Lightpanda migration continuation plan

Current qualified checkpoint: **2026-11-02 19:17 UTC / 20:17 CET**.
Production **0.13.1159** is installed and independently qualified. The
[supported full rollout](https://github.com/colophon-group/jobseek/actions/runs/ 41504447601)
succeeded at **19:06:11 UTC**, installing the contained redirect error fix, native
Go renderer acceptance client, remaining Python-client deletion and exactly eight
Michel boards in [PR #11494](https://github.com/colophon-group/jobseek/pull/11494).
All **8,298** enabled monitors are represented; all prior **8,290 / 8,288** board
keys and UUIDs are preserved. Runtime actors, natural canonical output and all
24 final controlled comparison arms qualify within the limits below.
The remaining delivery work is the reviewed stopped-asset cleanup and publication
of this portable checkpoint. Oracle ADF remains a separate compatibility follow-up,
not a global gate. The [portable receipt](evidence/go-native-installed-current.json)
records exact identities, the full seven-milestone audit and pending cleanup.

## Objective and language boundary

Preserve the full scope of the [original plan #10185](https://github.com/colophon-group/jobseek/pull/10185)
and [salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177):
finish native extraction/enrichment, monitor/detail/browser execution,
scheduling, canonical persistence, downstream publication, enabled-profile
coverage, runtime maintenance/deployment consumers and retirement. Keep
comparable whole-lane efficiency and supported cutover/reversal evidence.

Only performance-sensitive paths require Go. Useful low-frequency control,
provisioning/security, workspace/labeller and offline Python may remain.
Production transports stay **Go HTTP/API or latest self-hosted Lightpanda**.
Rollback uses the previous verified immutable Go release. Delete superseded
Python runtime implementations and images; preserve every enabled board,
publisher policy, fences/leases, canonical output and the demo reserve.

## Qualified installed state and natural effects

Independent **19:07:40 UTC** readback verifies the actual installed release:

| Installed crawler identity | Value |
| --- | --- |
| Version | `0.13.1159` |
| Runtime source | `78962559e4411ae0178fcf6e7680ce9f19765d0f` |
| Runtime contract | `bd6d173ccdbab1050b362f8af412552c01b06ef09bf8c0adb20bddfd438d48ff` |
| Immutable image | `sha256:d24f84b80424a7b6cee11fb1e92f7a2b290e7b182a47ada6344143e2f1506575` |
| Data source | `78962559e4411ae0178fcf6e7680ce9f19765d0f` |
| Data contract | `d03e52c61e167beed6cb425e2be5e04a72d594464775d75befbc708944929d0d` |

All four native actors and six core services run, with healthy checks where
configured and **zero actor restarts/OOM**. Format-v3 release, environment except
the documented `COMPOSE_FILE`, success/manifest and all eight CSV hashes match.
No legacy processor runs; no Python, uv, Playwright or Chromium runtime command
is present. Canonical companies **6,296**, epoch **404**, schema **0042** and zero
active migration plans are verified. The 1158 first fatal Peoplesoft response error
was corrected through contained response-error classification; origin allowlists
and fatal caller-configuration guards remain intact. Its exact failed response
Location was never established, and no retrospective cause is invented.

Independent **19:07:34 UTC** atomic observation finds all **8,298** enabled monitors
represented: **8,238 queued + 60 valid leases**, with zero unexplained gaps,
duplicates, wrong lanes, cache/domain mismatches or lease-token-presence gaps.
All previous 8,290 and 8,288 board keys and UUIDs are preserved. Token checks use
`HEXISTS`, never token values.

The bounded natural sample since native process start contains **420 fresh,
zero-failure records /338 boards /28 provider groups**, including **31 browser-configured
records**. All 420 have future due dates after scrape; missing titles and empty
current descriptions are zero. **415** current description hashes match and their
current descriptions are uploaded. These are current canonical/downstream effects,
not a universal per-record freshness, causal SLA or backlog-clearance claim.

Locale/title cardinality is not a global invariant. Existing `BuildLocales`
appends detected description languages independently of title variants; title-only
content without language/description can preserve empty locales. The original 420
sample reports 15 count differences. A separate cutoff-window observation of 437
records found 11 extra content languages and 4 empty locales / 4 missing hashes;
**it is not the same 420-record snapshot**. No data repair or new gate follows.
The [field reference](08-job-data-fields.md) now states the distinction.

The dated 1157 source/image-bound private-path check passed at **14:38 UTC** in
**0.717 seconds**: PostgreSQL `SELECT 1` and Typesense `GET /health` only.
It grants no provider, Justup, Redis/Timescale, role or ingress-installation authority.

[Incident #11489](https://github.com/colophon-group/jobseek/issues/11489)
tracks the [failed 1156 deployment](https://github.com/colophon-group/jobseek/actions/runs/ 41440630577).
Previous-release inspection failed before intended schema/sync cutover when
Docker rejected host networking with links. Rollback then failed a release-pointer
parent check. The precise link/trap context is not claimed established.
The failed release must not be blindly rerun.

The protected, independently reviewed `restore-native-1155-v2` operation restored
the complete previous Go release in **23.244 seconds**, with no forced termination
or restoration failures. Independent installed and queue checks followed. No
migration, sync, epoch or direct schedule write ran. The first recovery packet
refused before runtime mutation. The recovery and subsequent corrective 1157
installation are qualified. Closed incident drivers remain in reviewed Git history
rather than current source.
The 23.244-second recovery is separate from normal deployment timing; no new
Justup stage or API authority follows from the corrective release.

## Latest Lightpanda and capacity

The [normal renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/ 41399782403)
and independent binary/image/containment checks remain qualified:

| Installed renderer identity | Value |
| --- | --- |
| Lightpanda version | `1.1.0-nightly.11500+721618eeb` |
| Wrapper source | `a06edb6b088662c85c19d854c7c6d1a8d55ab3e0` |
| Wrapper image | `sha256:287a174a1a180cc58c027076284baeb03b10fcbfb6f69886bb3b6729bfd1790d` |
| Upstream source | `721618eebb6fb4b83d7cda0030a438f6c76be6c9` |
| Actual ARM64 binary SHA-256 | `4021e26730b2955154b24770812d50476b3259d9b0850f4da88e38519917f756` |

The [official nightly release](https://github.com/lightpanda-io/browser/releases/tag/nightly)
was rechecked at **18:54:34 UTC**: its latest ARM executable still matches the
installed binary; no newer nightly rollout was needed. The official stable API
was also checked at **19:02:25 UTC**: stable 1.0.2 was published October 23, older
than installed nightly 11500. Runtime/resource observations remain separately dated.
No new forced browser/provider work was performed.

Worker allocation remains **4 CPU / 2 GiB**, **1 GiB Go memory target**,
**120 runners / 30 monitor slots**. Renderer allocation remains
**1 CPU / 1 GiB / four shared slots**, zero swap and 64 PIDs. The dated
**17:12 UTC** renderer baseline shows unchanged allocation, zero restart/OOM
and memory-event deltas, **3.220 GiB RAM available / 9.626 GiB root free**.
Justup retains **0.5 CPU / 1 GiB**, its already allocated **2 GiB filesystem**
and **8 GiB root-free floor**; do not subtract the filesystem twice.

This checkpoint grants no Justup host operation or new owner probe. Resource
snapshots grant no API/stage activation, functional render/restart/reboot acceptance
or quota increase; the designated operator coordinates any separate window.
More crawler scaling needs
eligible successful unique-detail throughput and SQL/Redis, origin-policy,
search and renderer pressure together. CPU usage alone does not identify the
bottleneck. Preserve the demo reserve and publisher limits.

## Dated controlled comparison and acceptance audit

The [final 1159 comparison](https://github.com/colophon-group/jobseek/pull/11494/checks)
is bound to **`0a9b34254f506a5bb57a44895bbbf04f574390e1`**. Its tested tree
`36f894f7eb03652090047cbb6386348d1eed5bb6` matches merged source
`78962559e4411ae0178fcf6e7680ce9f19765d0f`.
All **24 arms** have canonical parity and successful cleanup:
four physical AMD64/ARM64 generated/captured scenarios, six alternating arms
per scenario, three per lane and nine actors per arm. Generated fixtures contain
536 postings; captured fixtures 72. Values below are means, **native / legacy**:

| Scenario | Elapsed seconds | CPU seconds | Sampled peak MiB |
| --- | --- | --- | --- |
| AMD64 generated | 19.754 / 32.350 | 11.102 / 21.025 | 456.40 / 908.76 |
| AMD64 captured | 35.492 / 67.632 | 17.485 / 21.836 | 425.48 / 817.09 |
| ARM64 generated | 20.494 / 32.087 | 10.366 / 19.235 | 467.54 / 920.54 |
| ARM64 captured | 34.077 / 65.708 | 11.500 / 14.700 | 427.44 / 825.14 |

The table uses reported measured CPU; the full CI report records startup/lifetime
counters separately. Sampled peaks are lower bounds, modeled cost is not invoiced
cost, and the equal-fixture TLS exception is not production
TLS CPU accounting. Per-arm exit codes are unavailable, not assumed zero.
Fixture budgets differ from production. The first AMD64 generated build failed
before measurement; a same-head failed-job-only retry passed with unchanged
assertions. All 24 final measured arms pass. Required CI job 125479353346 and
Crawler Deploy Gate succeeded. The separate 1157 correction also passed real
Docker maintenance proof. Measured, startup and lifetime CPU remain separate.

The complete original acceptance scope remains:

| Area | Qualified scope and remaining limit |
| --- | --- |
| Salary extraction/conversion | Native historical effects: 3,502 operations, 394 fresh rows, 76 boards/14 locales, five fields and zero drift. Component changes since that witness require current conformance; no byte-identity claim. |
| Location/CPU stages | 80,641 historical native resolutions and 140 browser-cohort rows; the old browser caller was Python. Current native caller ownership is verified separately. |
| Lightpanda executor and ownership | Native image/claims/fences/failure/schedule/crash contracts and latest binary installed; earlier interruption causes remain unproven. |
| Ordinary processing/export/drain | Four native 1159 actors, zero restarts/OOM; 420 bounded fresh natural canonical records and 415 current hash/upload matches. Old backlog and one dated inactive-description metadata discrepancy remain explicit. |
| Enabled profiles and original title case | All current 8,298 monitor identities represented; previous 8,290/8,288 floors preserved. Prior named/title/provider witnesses remain dated; ADF stays enabled and separate. |
| Python implementation retirement | Superseded daemon modules/image targets removed through 11488/1157; 1159 installs native manual acceptance and deletes its Python client. Useful control/offline Python remains permitted. |
| Efficiency/cutover/reversal/completion | Final 24 same-workload controls, native 1159 installation and bounded natural effects qualified. Dated 1157 private-path/timing and Go-to-Go cold reversal retained. Reviewed cleanup and portable publication remain. |

The [52-minute normal-traffic qualification](evidence/go-native1149-steady-2026-11-01.json)
showed headroom after scaling but retained substantial pre-existing detail backlog.
Current observation deliberately omits expensive detail-demand SQL. Skips,
posting touches, Redis totals and a single resource snapshot do not prove demand is met.

## Next delivery steps

1. **Publish the qualified combined release checkpoint.** PR #11494 delivered
   the redirect response-error classification, native Go acceptance/Python-client
   deletion and exactly **eight Michel boards / one company / one description**.
   Candidate head `0a9b34254f506a5bb57a44895bbbf04f574390e1` and merged source
   `78962559e4411ae0178fcf6e7680ce9f19765d0f` have the same tested tree
   `36f894f7eb03652090047cbb6386348d1eed5bb6`. Zero prior 8,290 CSV rows changed
   or were removed. Required CI, Deploy Gate, all final comparisons and supported
   rollout succeeded; actual installed identities, queues and natural effects are
   independently qualified. No new full runtime deployment is needed for these
   docs. Host manual-render/restart/reboot workflow execution remains ungranted.

2. **Preserve the shortened maintenance path and its authority checks.**
   Authenticated previous-Go-image inspection runs with required credentials and
   read-only data while workers serve. Standalone native maintenance avoids
   Compose one-off links and root/env shadowing in ERR/EXIT traps. Repair remains
   after quiescence, sync and epoch attestation, with a fresh snapshot, ownership
   checks each page and atomic per-schedule CAS. Earlier inspection grants no
   write authority. The same observable stop-completion-to-worker-start markers
   measured **203.965 seconds for 1155 versus 141.265 seconds for 1157**, a
   **62.700-second reduction**. This excludes preceding graceful drain and final
   readiness/promotion, covers one rollout and is neither the whole writer pause
   nor a future SLA. The 23-second recovery is a different operation. Preserve
   preflight, lock, complete rollback and readiness.

3. **Remove closed legacy assets through the separate reviewed maintenance packet.**
   The **17:49 UTC** source-bound inventory found five stopped legacy services:
   `worker-1`, `worker-2`, `worker-3`, `browser-1` and `lightpanda-claimant`, stopped
   since October 29. The **17:57 UTC** retained-image inventory covered all **70**
   retained generation directories and **282** release metadata files. All **18**
   unique immutable SHA pins resolve; neither of the **two old legacy images**
   matches a retained pin or resolved release image. This is read-only inventory,
   **not coherent locked deletion clearance**. Root is preparing a separate
   independently reviewed exact maintenance packet; 1159 is now qualified, but
   the cleanup execution receipt is still pending. The packet must
   recheck exact stopped identities and coherent active/rollback/spec/retained
   image protection under the maintenance lock. No broad prune, active asset
   deletion or required rollback-asset deletion is authorized by this checkpoint.

4. **Keep the original requirements reconciliation faithful.** Preserve all seven
   milestones, salary/enrichment, named/profile cases, ownership, downstream effects,
   efficiency and dated natural/reversal evidence. Captured same-workload comparisons
   plus bounded natural database effects are the evidence delivered. There is no
   paired-production/global causal SLA or backlog-zero gate. No historical Python
   observation-duration receipt is claimed: the latest explicit human deletion
   instruction supersedes a pending local window agreement. Preserve the verified
   immutable native Go rollback. Do not invent an extra soak quota or waiting gate,
   universal freshness, causal production speedup or invoiced saving.

## Separate ADF compatibility follow-up

The **19:07 UTC** snapshot still records the original Oracle ADF board enabled,
unreserved and quarantined after its normal November 1 pagination HTTP 302 failure;
failures **12**, with the scheduled natural retry **November 2 17:37:17 UTC /
18:37:17 CET** timestamp. No successful retry is inferred from that unchanged state. Do not force work or reset
publisher policy, schedules, counters or backoff. Native 1102 evidence records
52 jobs/zero failures on October 27; last-success October 28 alone does not
establish runtime ownership of that attempt.

The authorized local Lightpanda More control made **58 requests / zero details**;
the separately authorized fresh Chrome control made **110 requests / one More
click / zero details**. Both retained **25 rows, index 24 and More**, with two
pagination redirects and empty status-zero XHR results. This establishes neither
a complete inventory nor a Lightpanda-specific, universal publisher or CORS/session
cause. No speculative form/header/window patch is qualified. The Chrome exception
is consumed and temporary artifacts were removed; production stays Go/Lightpanda.
[Existing follow-up #10790](https://github.com/colophon-group/jobseek/issues/10790#issuecomment-6563361945)
holds the evidence. Re-enter on a changed natural result, supported complete source,
working session/environment witness or relevant upstream correction. Any authorized
local verification remains bounded to 500 jobs / 5,000 detail checks.

The [supported Go-to-Go cold reversal](https://github.com/colophon-group/jobseek/actions/runs/ 41213487268)
is dated 1137 evidence, not a new retirement/corrective exercise. Keep the previous
verified immutable Go release and exact data/spec rollback evidence. Follow
[ADR-006](adr/006-crawler-deploy-quiescence-and-rollback.md) and normal holds/locks;
no rsync, mutable deployment or partial writer restart. Keep one current checkpoint,
active regressions and supported rollback assets. Remove owned closed temporary
assets/caches; preserve shared dependencies, contributors' files and `dump.rdb`.
