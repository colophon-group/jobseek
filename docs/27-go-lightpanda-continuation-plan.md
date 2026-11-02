# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-11-02 18:04 UTC / 19:04 CET**.
Production **0.13.1158** is installed: its
[supported rollout](https://github.com/colophon-group/jobseek/actions/runs/41487771596)
succeeded at **17:26:15 UTC**. All **8,290** enabled monitors are represented and
the full previous 8,288-board identity floor is preserved. Full runtime qualification
requires a correction for the first fatal `detail_execution:configuration` error
in `peoplesoft`: the ordinary worker restarted once, then remained running with no
OOM or panic. The exact failed response Location remains unproven.
The latest fully qualified release remains **1157**; its Python daemon retirement
and bounded maintenance improvement remain valid dated evidence. Full migration
is active. Oracle ADF remains a separate compatibility follow-up, not a global gate.
The [portable receipt](evidence/go-native-installed-current.json) separates the
installed 1158 assessment from qualified 1157 evidence and pending
[combined 1159 PR #11494](https://github.com/colophon-group/jobseek/pull/11494).

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

## Current installed assessment and qualified release

Independent **17:26:32 UTC** readback verifies the actual installed release:

| Installed crawler identity | Value |
| --- | --- |
| Version | `0.13.1158` |
| Runtime source | `42140b870f459e5aada974025757c190e162c6ee` |
| Runtime contract | `549a5972e6ae4dea3a8c4dc41b253414d8bea6a24f7796898896d66de0ae26bb` |
| Immutable image | `sha256:73e259b1832a2de782bf0f3cc6733bf9e67f2017a33c5c5229c2b54ea1687ea4` |
| Data source | `42140b870f459e5aada974025757c190e162c6ee` |
| Data contract | `0642eafb10a008dbb919baef224b77afe9673baf471713df76865a9bc034e48c` |

All four native actors and six core services run, with healthy checks where
configured. The ordinary worker has **one restart**, stable through the **17:29**
observation; no actor OOM and no worker panic were found. The first fatal diagnostic
is `detail_execution:configuration profile=peoplesoft`. First-run fence evidence
covers only the new company and does not establish the exact failed response
Location. Bounded natural Monte-Carlo output includes **42 fresh successful details /
48 active postings** at **17:33:46 UTC**, which does not resolve that attribution.
This real runtime error is the remaining 1158 qualification blocker; successful
workflow completion and controlled parity do not replace runtime qualification.

Format-v3 release, environment except the documented `COMPOSE_FILE`, success/manifest
and all eight CSV hashes match. No legacy processor runs; no Python, uv, Playwright
or Chromium runtime command is present. Canonical companies **6,295**, epoch **404**,
schema **0042** and zero active migration plans are verified.

Independent **17:26:31 UTC** atomic observation finds all **8,290** enabled monitors
represented: **8,223 queued + 67 valid leases**, with zero unexplained gaps,
duplicates, wrong lanes, cache/domain mismatches or lease-token-presence gaps.
All previous 8,288 board keys and UUIDs are preserved. Token checks use `HEXISTS`,
never token values. These are dated monitor/schedule facts, not universal posting
freshness or backlog completion results.

The latest fully qualified snapshot is **1157**, source
`a32864661b902b5786139e1fdbb69716e53da3b4`, image
`sha256:e589e4fd508de8604af19acb5f9af389d1e2f059ece3f6c8993bd1b901a05924`.
Its [normal rollout](https://github.com/colophon-group/jobseek/actions/runs/41455879195)
succeeded at **14:01:12 UTC**, followed by independent installed and queue checks.
Its source/image-bound private-path verification passed at **14:38 UTC** in
**0.717 seconds**: PostgreSQL `SELECT 1` and Typesense `GET /health` only.
That dated check grants no provider, Justup, Redis/Timescale, role or ingress-installation
qualification for 1158.

[Incident #11489](https://github.com/colophon-group/jobseek/issues/11489)
tracks the [failed 1156 deployment](https://github.com/colophon-group/jobseek/actions/runs/41440630577).
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

The [normal renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/41399782403)
and independent binary/image/containment checks remain qualified:

| Installed renderer identity | Value |
| --- | --- |
| Lightpanda version | `1.1.0-nightly.11500+721618eeb` |
| Wrapper source | `a06edb6b088662c85c19d854c7c6d1a8d55ab3e0` |
| Wrapper image | `sha256:287a174a1a180cc58c027076284baeb03b10fcbfb6f69886bb3b6729bfd1790d` |
| Upstream source | `721618eebb6fb4b83d7cda0030a438f6c76be6c9` |
| Actual ARM64 binary SHA-256 | `4021e26730b2955154b24770812d50476b3259d9b0850f4da88e38519917f756` |

The [official nightly release](https://github.com/lightpanda-io/browser/releases/tag/nightly)
was rechecked at **17:12:36 UTC**: its latest ARM executable still matches the
installed binary; no newer rollout was needed. Runtime/resource observations
remain separately dated. No new forced browser/provider work was performed.

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

The [current 1158 comparison](https://github.com/colophon-group/jobseek/pull/11491/checks)
is bound to **`d2ddfb5204dabd40d8d1c15b47a9acbdc82285a6`**. Its tested tree
`a51ce5c3458826723ded353a30e29e52f72d05fc` matches the tree of merged source
`42140b870f459e5aada974025757c190e162c6ee`.
All **24 arms** have canonical parity and successful cleanup:
four physical AMD64/ARM64 generated/captured scenarios, six alternating arms
per scenario, three per lane and nine actors per arm. Generated fixtures contain
536 postings; captured fixtures 72. Values below are means, **native / legacy**:

| Scenario | Elapsed seconds | CPU seconds | Sampled peak MiB |
| --- | --- | --- | --- |
| AMD64 generated | 19.656 / 34.763 | 13.928 / 25.810 | 448.59 / 912.88 |
| AMD64 captured | 35.475 / 67.488 | 17.636 / 22.238 | 423.78 / 820.46 |
| ARM64 generated | 19.735 / 32.189 | 10.109 / 19.164 | 459.22 / 924.39 |
| ARM64 captured | 34.021 / 64.631 | 11.204 / 14.281 | 418.94 / 824.90 |

The table uses reported measured CPU; the full CI report records startup/lifetime
counters separately. Sampled peaks are lower bounds, modeled cost is not invoiced
cost, and the equal-fixture TLS exception is not production
TLS CPU accounting. Per-arm exit codes are unavailable, not assumed zero.
Fixture budgets differ from production. These successful 1158 controls do not
qualify stability after the real fatal restart. The separate 1157 correction passed
Required CI, Crawler Deploy Gate and real Docker maintenance proof before its
installed qualification. Measured, startup and lifetime CPU remain separate.

The complete original acceptance scope remains:

| Area | Qualified scope and remaining limit |
| --- | --- |
| Salary extraction/conversion | Native historical effects: 3,502 operations, 394 fresh rows, 76 boards/14 locales, five fields and zero drift. Component changes since that witness require current conformance; no byte-identity claim. |
| Location/CPU stages | 80,641 historical native resolutions and 140 browser-cohort rows; the old browser caller was Python. Current native caller ownership is verified separately. |
| Lightpanda executor and ownership | Native image/claims/fences/failure/schedule/crash contracts and latest binary installed; earlier interruption causes remain unproven. |
| Ordinary processing/export/drain | Four native actors installed. 1158 first fatal Peoplesoft configuration-classified response error requires correction. Canonical/search/R2 effects remain sampled/dated; old backlog and one inactive-description metadata discrepancy remain explicit. |
| Enabled profiles and original title case | All current 8,290 monitor identities represented and previous 8,288 floor preserved. Prior named/title/provider witnesses remain dated; ADF stays enabled and separate. |
| Python implementation retirement | Eleven superseded daemon modules and Python image targets removed in qualified 1157; useful control/offline tooling retained. Manual renderer acceptance follow-up remains source-only. |
| Efficiency/cutover/reversal/completion | 1157 installation/private paths/timing qualified; 1158 controls and identity floor verified, runtime correction pending. Dated whole-lane controls and Go-to-Go cold reversal retained. Combined 1159 consumer/fix and final audit remain. |

The [52-minute normal-traffic qualification](evidence/go-native1149-steady-2026-11-01.json)
showed headroom after scaling but retained substantial pre-existing detail backlog.
Current observation deliberately omits expensive detail-demand SQL. Skips,
posting touches, Redis totals and a single resource snapshot do not prove demand is met.

## Next delivery steps

1. **Finish the exact-head combined 1159 PR and one full release.**
   [PR #11494](https://github.com/colophon-group/jobseek/pull/11494) contains the
   publisher redirect response-error classification fix, native Go renderer
   acceptance client and superseded Python client deletion, plus **exactly eight
   Michel boards / one company / one description**. Candidate total is **8,298**
   enabled boards; **zero previous 8,290 CSV rows changed or were removed**.
   Independent combined-source review passed with no blocking findings.

   | Candidate 1159 identity | Value |
   | --- | --- |
   | Exact PR head | `0a9b34254f506a5bb57a44895bbbf04f574390e1` |
   | Base | `42140b870f459e5aada974025757c190e162c6ee` |
   | Tree | `36f894f7eb03652090047cbb6386348d1eed5bb6` |
   | Runtime contract | `bd6d173ccdbab1050b362f8af412552c01b06ef09bf8c0adb20bddfd438d48ff` |
   | Data contract | `d03e52c61e167beed6cb425e2be5e04a72d594464775d75befbc708944929d0d` |

   CI is still in progress at the operator update: **45 successful checks** and
   no Required CI failure reported yet. **One AMD64 generated comparison build
   failed before measurement in the unchanged `ats-inventory` stage**; Intake
   is assessing sibling results and a targeted retry for a possible transient
   failure. No green current 1159 24-arm result is claimed. Complete the fresh
   exact-head checks and merge audit before a supported full release. Preserve
   origin allowlists and fatal caller-configuration guards. Independently qualify
   corrected actors, source/image/data, all 8,298 identities and the complete
   prior 8,290 key/UUID floor. The real 1158 fatal restart remains the runtime
   qualification issue; this checkpoint adds no new runtime hold or arbitrary soak.
   No host manual-render/restart/reboot acceptance execution is granted.

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
   independently reviewed maintenance packet for after 1159 qualifies. It must
   recheck exact stopped identities and coherent active/rollback/spec/retained
   image protection under the maintenance lock. No broad prune, active asset
   deletion or required rollback-asset deletion is authorized by this checkpoint.

4. **Publish the full acceptance audit with explicit original evidence limits.**
   Preserve all seven milestones, salary/enrichment, named/profile cases, ownership,
   downstream effects, efficiency and dated natural/reversal evidence. No agreed
   Python rollback observation duration or receipt was found; the latest human
   instruction explicitly authorized deleting the superseded implementation, and
   the historical duration criterion is not claimed proved. Unpaired production
   windows also do not prove global no-material-freshness-regression. Existing
   bounded canonical/downstream effects, mixed natural work, controlled parity and
   supported cold reversal remain valid within their recorded scope. Do not invent
   a soak quota or hold, universal posting freshness or cleared demand.

## Separate ADF compatibility follow-up

The **17:26 UTC** snapshot records the original Oracle ADF board enabled,
unreserved and quarantined after its normal November 1 pagination HTTP 302 failure;
failures **12**, with the scheduled natural retry **November 2 17:37:17 UTC /
18:37:17 CET**. No later natural result is claimed here. Do not force work or reset
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

The [supported Go-to-Go cold reversal](https://github.com/colophon-group/jobseek/actions/runs/41213487268)
is dated 1137 evidence, not a new retirement/corrective exercise. Keep the previous
verified immutable Go release and exact data/spec rollback evidence. Follow
[ADR-006](adr/006-crawler-deploy-quiescence-and-rollback.md) and normal holds/locks;
no rsync, mutable deployment or partial writer restart. Keep one current checkpoint,
active regressions and supported rollback assets. Remove owned closed temporary
assets/caches; preserve shared dependencies, contributors' files and `dump.rdb`.
