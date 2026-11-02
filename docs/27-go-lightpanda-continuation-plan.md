# Go and Lightpanda migration continuation plan

Current qualified checkpoint: **2026-11-02 13:48 UTC / 14:48 CET**.
Production is the fully restored **0.13.1155** Go release. Python daemon source
retirement is merged in [PR #11488](https://github.com/colophon-group/jobseek/pull/11488),
but its 1156 deployment failed. Corrective [PR #11490](https://github.com/colophon-group/jobseek/pull/11490)
is merged as `a32864661b902b5786139e1fdbb69716e53da3b4`; its supported
[1157 rollout](https://github.com/colophon-group/jobseek/actions/runs/41455879195)
was building at this checkpoint and **is not qualified installed**. Full migration remains active.
Oracle ADF is a separate compatibility follow-up, not a global delivery gate.
The [current qualification](evidence/go-native-installed-current.json) records
installed identities, conservation, the incident/recovery and explicit limits.

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

## Qualified installed state and recovered release

The [normal 1155 rollout](https://github.com/colophon-group/jobseek/actions/runs/41424740467)
succeeded at **10:14:08 UTC**. After the failed retirement rollout, a separately
reviewed full-release recovery restored this same runtime. Independent
**12:43:35 UTC** readback verifies:

| Installed crawler identity | Value |
| --- | --- |
| Version | `0.13.1155` |
| Runtime source | `2a200c349ffdf0df3899e1ac38dea83e32031bbe` |
| Runtime contract | `ace0d2d14d2f72a1a8edea8fe5dddea1ac266aec804117f8b0a95cf26fdf1a7a` |
| Immutable image | `sha256:c7e76a2d4abfeb8c8ab5962d566e2669b73e9dfe33fe497ad02299850fee2b3a` |
| Data source | `909e86b156af479a0e93c5775a002811444b0c27` |
| Data contract | `f345cd4096083e7b1ee5880acf6a4a1ae289079a5b11d1255dbce1721154690b` |

All four native actors and six core services run, with healthy checks where
configured and zero actor restarts/OOM. Format-v3 release, environment except
the documented `COMPOSE_FILE`, success/manifest and all eight CSV hashes match.
No legacy processor runs; the image exposes no Python, uv, Playwright or
Chromium command. Epoch **404**, schema **0042** and zero active migration plans remain.

Independent **12:44:13 UTC** atomic observation finds all **8,288** enabled
monitors represented: **8,261 queued + 27 valid leases**, with zero unexplained
gaps, duplicates, wrong lanes, cache/domain mismatches or lease-token-presence
gaps. Every pre-retirement key and UUID matches the full 8,288-board baseline.
Token checks use `HEXISTS`, never token values. These are dated monitor/schedule
facts, not universal posting-freshness or backlog-completion results.

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
refused before runtime mutation. The recovery is complete; company publication
and Justup stages await the corrective release's qualification.

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
was rechecked at **13:00:23 UTC**: its latest ARM executable still matches the
installed binary; no newer rollout was needed. Runtime/resource observations
remain separately dated. No new forced browser/provider work was performed.

Worker allocation remains **4 CPU / 2 GiB**, **1 GiB Go memory target**,
**120 runners / 30 monitor slots**. Renderer allocation remains
**1 CPU / 1 GiB / four shared slots**, zero swap and 64 PIDs. The dated
**11:45 UTC** renderer baseline shows unchanged allocation, zero restart/OOM
and memory-event deltas, **3.150 GiB RAM available / 9.635 GiB root free**.
Justup retains **0.5 CPU / 1 GiB**, its already allocated **2 GiB filesystem**
and **8 GiB root-free floor**; do not subtract the filesystem twice.

There is no active Justup host grant or new owner probe. A resource snapshot
or released metadata-only window grants no API/stage activation, functional
render/restart/reboot acceptance or quota increase. More crawler scaling needs
eligible successful unique-detail throughput and SQL/Redis, origin-policy,
search and renderer pressure together. CPU usage alone does not identify the
bottleneck. Preserve the demo reserve and publisher limits.

## Dated controlled comparison and acceptance audit

The [corrective-source comparison](https://github.com/colophon-group/jobseek/pull/11490/checks)
is bound to **`cc369d37d0cf850b81c3a97feecbfddc581740ac`**. Its tested tree
equals merged source `a32864661b902b5786139e1fdbb69716e53da3b4`.
All **24 arms** have canonical parity and successful cleanup:
four physical AMD64/ARM64 generated/captured scenarios, six alternating arms
per scenario, three per lane and nine actors per arm. Generated fixtures contain
536 postings; captured fixtures 72. Values below are means, **native / legacy**:

| Scenario | Elapsed seconds | CPU seconds | Sampled peak MiB |
| --- | --- | --- | --- |
| AMD64 generated | 19.230 / 33.777 | 12.403 / 23.139 | 455.17 / 935.36 |
| AMD64 captured | 35.565 / 67.583 | 17.808 / 22.670 | 422.60 / 814.60 |
| ARM64 generated | 19.489 / 30.132 | 9.988 / 18.796 | 465.76 / 921.64 |
| ARM64 captured | 33.902 / 65.730 | 11.956 / 15.082 | 429.20 / 824.70 |

The table uses reported measured CPU; the full CI report records startup/lifetime
counters separately. Sampled peaks are lower bounds, modeled cost is not invoiced
cost, and the equal-fixture TLS exception is not production
TLS CPU accounting. Per-arm exit codes are unavailable, not assumed zero.
Fixture budgets differ from production. Exact-head Required CI, Crawler Deploy
Gate and the real Docker maintenance proof passed; installed-state qualification
remains pending. Measured, startup and lifetime CPU remain separate in the report.

The complete original acceptance scope remains:

| Area | Qualified scope and remaining limit |
| --- | --- |
| Salary extraction/conversion | Native historical effects: 3,502 operations, 394 fresh rows, 76 boards/14 locales, five fields and zero drift. Component changes since that witness require current conformance; no byte-identity claim. |
| Location/CPU stages | 80,641 historical native resolutions and 140 browser-cohort rows; the old browser caller was Python. Current native caller ownership is verified separately. |
| Lightpanda executor and ownership | Native image/claims/fences/failure/schedule/crash contracts and latest binary installed; earlier interruption causes remain unproven. |
| Ordinary processing/export/drain | Four native actors installed; canonical/search/R2 output evidence remains sampled and dated. Old backlog and one inactive-description metadata discrepancy remain explicit. |
| Enabled profiles and original title case | All current 8,288 monitor identities preserved. Prior named/title/provider witnesses remain dated; ADF stays enabled and separate. |
| Python implementation retirement | Source deletions merged; useful control/offline tooling retained. Final corrected release remains unqualified. |
| Efficiency/cutover/reversal/completion | Dated whole-lane controls and supported Go-to-Go cold reversal retained. Corrective rollout, manual-consumer follow-up and final portable reconciliation remain. |

The [52-minute normal-traffic qualification](evidence/go-native1149-steady-2026-11-01.json)
showed headroom after scaling but retained substantial pre-existing detail backlog.
Current observation deliberately omits expensive detail-demand SQL. Skips,
posting touches, Redis totals and a single resource snapshot do not prove demand is met.

## Next delivery steps

1. **Qualify the supported corrective 1157 rollout.** PR #11490 head
   `cc369d37d0cf850b81c3a97feecbfddc581740ac` passed Required CI, Crawler Deploy
   Gate, real Compose rendering/Docker create-inspect proof and all four
   comparisons. The normal head-bound merge produced
   `a32864661b902b5786139e1fdbb69716e53da3b4`, tree
   `f071b5ab55d6b2700724f07a365536b395a095b4`, matching reviewed/tested source.
   Runtime contract is `cac6e8bfa0d3d073dbf4fd3b2c6cce741a630f2e5af65124e16496939ed6c3ee`.
   Full rollout **41455879195** is in progress; **1157 is not yet qualified
   installed**. Finish that supported immutable workflow and independently
   verify installed image/data, board identities, queue conservation and timing.

2. **Verify the shortened writer pause.** Inspect with the authenticated previous
   Go image, required credentials and read-only data while workers serve.
   Corrective source uses standalone native Docker maintenance, avoiding Compose
   one-off links and root/env shadowing in ERR/EXIT traps. Repair remains after
   quiescence, sync and epoch attestation, with a fresh snapshot, ownership checks
   each page and atomic per-schedule CAS. Earlier inspection grants no write
   authority. The previous normal 1155 stop-completion-to-worker-start interval
   was about **204 seconds**, excluding graceful drain and final readiness.
   Corrective rollout savings are not yet measured; the 23-second recovery is a
   different operation. Preserve preflight, lock, complete rollback and readiness.

3. **Ship the separate manual renderer consumer.** Reviewed native acceptance
   source `32fb5e606d3478f8a4ca57e889bf0368e54ab894` is prepared on merged 1156.
   Publish after the coordinated company successors at the actual next VERSION.
   Do not delay their work for this follow-up. It has no PR/rollout yet, and no
   public render, restart or host reboot workflow execution is authorized.

4. **Close the full acceptance audit on the final source.** Preserve every enabled
   board, canonical output, publisher policy, fences/leases and downstream
   publication. Reconcile original salary/enrichment, profile, ownership,
   efficiency and dated natural-effects/reversal evidence. Update this report
   with the qualified final source/image/queue/timing; do not claim universal
   posting freshness or cleared demand.

## Separate ADF compatibility follow-up

The original Oracle ADF board remains enabled, unreserved and quarantined after
its normal November 1 pagination HTTP 302 failure; failures **12**. Its unchanged
next retry is **November 2 17:37:17 UTC / 18:37:17 CET**. Do not force work or reset
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
