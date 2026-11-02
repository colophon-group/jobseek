# Go and Lightpanda migration continuation plan

Current checkpoint: **2026-11-02 06:36 UTC / 07:36 CET**. The performance-sensitive
migration is implemented and installed, but **full delivery remains blocked on
Oracle ADF pagination/freshness**. Start from the current installed identities and
canonical registry, rather than saved counts. The
[current qualification](evidence/go-native-installed-current.json) binds the
installed releases, schedule conservation, controlled comparisons and remaining limits.

## Scope and installed checkpoint

Only performance-sensitive paths require Go: crawler execution,
extraction/enrichment, scheduling/queue/persistence, export and description drain.
Low-frequency control, provisioning/security, workspace/labeller/offline tools
and required cold-reversal Python may remain. Production transports stay
**Go HTTP/API or latest self-hosted Lightpanda; no Chromium fallback**.

The [normal 0.13.1153 deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/41384055528/attempts/2)
completed at **06:19:57 UTC**. Independent **06:20:31–33 UTC** readback verifies:

| Installed crawler identity | Value |
| --- | --- |
| Version | `0.13.1153` |
| Runtime/data source | `9b5397482e57052eb3f53eac7f731d0d74f5dafe` |
| Runtime contract | `7e5f1621c592260cab8ef46ef86a8fcc834e82b2e6440c4f11e1e44f135c23b3` |
| Immutable image | `sha256:65050831eea4d9ac081fee0f962328cecd8d968a5429dce2306d225ad7a11064` |
| Data contract | `388889b6039f9b5aacf661a690b75f3c738bc5bc26819091d61523c5717ef6ad` |

All four native actors and six core services pass their installed checks, with
zero actor restarts/OOM. Active format-v3 release, complete environment except
the documented `COMPOSE_FILE`, success/manifest evidence and all eight CSV hashes
match. No legacy processor runs; the processing image exposes no Python, uv,
Playwright or Chromium command. Epoch **404**, schema **0042** and zero active
migration plans remain.

All **8,273** enabled monitors are represented atomically: **8,244 queued plus
29 valid leases**, zero gaps/duplicates/wrong lanes/cache/domain/deadletter or
lease-token-presence gaps. Every original pre-capacity **8,270** key/UUID remains,
plus three additions; hashes are retained in the qualification. Token existence
uses `HEXISTS`; token values are never read. Refresh the registry and installed
identity after any publication rather than freezing these totals in tests.

The failed first 1153 attempt stopped at the backup guard before replacing
workloads. One supported backup retry recovered health; the normal preflight
then accepted attempt 2. [Incident #11478](https://github.com/colophon-group/jobseek/issues/11478)
is closed. The historical repository-lock cause remains unproven; no guard,
lock/timer/timeout override or permanent source fix is claimed.

## Latest Lightpanda and demo reserve

The [normal renderer workflow](https://github.com/colophon-group/jobseek/actions/runs/41399782403)
completed at **06:27:51 UTC**. Independent **06:30:12 UTC** readback verifies:

| Installed renderer identity | Value |
| --- | --- |
| Lightpanda version | `1.1.0-nightly.11500+721618eeb` |
| Wrapper source | `a06edb6b088662c85c19d854c7c6d1a8d55ab3e0` |
| Immutable wrapper image | `sha256:287a174a1a180cc58c027076284baeb03b10fcbfb6f69886bb3b6729bfd1790d` |
| Upstream source | `721618eebb6fb4b83d7cda0030a438f6c76be6c9` |
| Actual ARM64 binary SHA-256 | `4021e26730b2955154b24770812d50476b3259d9b0850f4da88e38519917f756` |

All three official executable assets and both immutable Linux OCI binary layers
were independently matched. [PR #11480](https://github.com/colophon-group/jobseek/pull/11480)
passed both physical architectures, all four complete comparisons, ARM B0,
Required CI and Crawler Deploy Gate. Its tested tree equals the merged tree.
The installed command, binary, image/source labels, private cgroup, readonly root,
capability limits, no-new-privileges and mTLS flags pass independent checks.

Worker limits remain **4 CPU / 2 GiB**, **1 GiB Go memory target**, **120 discovery /
30 monitor slots**. Renderer limits remain **1 CPU / 1 GiB / four slots**, zero swap
and 64 PIDs. Justup owners coordinated the rollout and retained their separate
**0.5 CPU / 1 GiB**, already allocated **2 GiB filesystem** and **8 GiB root free
floor**. Postrollout available RAM is **3.214 GiB**, root free **9.645 GiB**; do not
subtract the filesystem twice. The new renderer has zero restarts/OOM/memory events.
Generation changed as intended; no lifetime counter delta is claimed across it.
There is no Docker health check or new forced functional-render/provider probe.

The renderer mutation window is released. Justup owners received the new immutable
image/generation and refrozen direct observer. Their future stage/API authority,
fresh baseline, outer deadline and feed-reader security gates remain separate.
No Justup API activation or P060 improvement follows from this proof.

## Current controlled comparison

The [latest complete-lane comparison](https://github.com/colophon-group/jobseek/actions/runs/41387202469)
uses six alternating arms per scenario, three per lane, nine actors per arm and
identical canonical output. Generated fixtures contain 536 postings; captured
fixtures 72. Values below are means, **native / legacy**:

| Scenario | Elapsed seconds | CPU seconds | Sampled peak MiB |
| --- | --- | --- | --- |
| AMD64 generated | 20.066 /35.831 | 15.100 /28.110 | 460.59 /947.11 |
| AMD64 captured | 35.719 /66.533 | 14.928 /19.752 | 426.27 /816.45 |
| ARM64 generated | 19.272 /30.854 | 10.030 /18.856 | 463.87 /944.16 |
| ARM64 captured | 34.087 /65.720 | 11.713 /14.926 | 424.76 /817.55 |

All arms report successful cleanup; per-arm exit codes are unavailable rather
than assumed zero. Startup/lifecycle CPU is included. Sampled peaks are lower
bounds, modeled cost is not invoiced cost, and the equal-fixture TLS exception is
not production TLS CPU accounting. Fixture budgets differ from the scaled worker.
These controls do not establish universal production freshness or a causal
mixed-workload speedup.

The [previous 52-minute normal-traffic qualification](evidence/go-native1149-steady-2026-11-01.json)
showed headroom after scaling, but substantial pre-existing detail backlog
remained. The 1153 observer deliberately omits expensive detail-demand SQL.
Before another scale-up, measure successful unique eligible detail throughput,
per-host scheduling/throttling and PostgreSQL/search/renderer pressure together.
Do not infer demand is met from skips, posting touches, Redis entry totals or a
single CPU/memory snapshot; preserve the demo reserve and publisher limits.

## Remaining delivery work

1. **Resolve the original Oracle ADF board.** It remains enabled, unreserved and
   quarantined after the normal November 1 **17:37:14 UTC** attempt failed with
   pagination HTTP 302; failures 12. Its unchanged next retry is November 2
   **17:37:17 UTC /18:37:17 CET**. Do not force work or reset counters/backoff.
   Dated native 1102 evidence records 52 jobs/zero failures on October27 at 15:27 UTC;
   last-success October28 alone does not establish which runtime owned that attempt.
   Current source/caller reconciliation finds no ADF request-logic regression
   through quarantine, but does not reconstruct historical image/environment/egress.

   The operator-authorized latest local Lightpanda native More control made
   **58 requests / zero details**. The separately authorized local Chrome 156
   control made **110 requests / one More click / zero details** in a fresh
   temporary profile. Both retained **25 rows, fetch index 24 and More**, with
   two pagination redirects, XHR status 0/empty body/no response URL. Chrome's URL
   and form window IDs matched; Lightpanda's did not. Since both still failed,
   that difference is not a sufficient explanation. This does not isolate the
   failure to Lightpanda, prove a universal publisher outage or identify a CORS/
   session root cause. Neither result qualifies a complete inventory.

   The advertised form-action patch, full publisher request/cookies, standard
   AJAX headers and framework window controls still have no verified complete
   Go result. Do not ship them speculatively, accept a partial 25-row inventory,
   blindly follow redirects or repeat guessed header/form/window variants.
   [Existing follow-up](https://github.com/colophon-group/jobseek/issues/10790#issuecomment-6563361945)
   carries the current evidence. Re-enter with a changed natural result,
   publisher-supported complete source, working environment/session witness or
   relevant upstream correction. Local verification remains bounded to 500 jobs /
   5,000 detail checks; the Chrome exception is consumed and never authorizes a
   production fallback. Its profile, executable and build cache were removed.

2. **Close the original acceptance audit after that outcome.** Preserve every
   enabled board, canonical title/description/location/enrichment output,
   publisher policy, fences/leases/schedules and downstream publishing. Existing
   dated qualifications cover the five former deadletters and the original
   required-title native BambooHR/API route; the current receipt keeps their
   limits, existing backlog and historical inactive-description metadata gap
   explicit. Batch related performance-path fixes when new evidence supports
   them; do not expand into rewriting approved Python control/offline tools.
   Use a runtime VERSION bump, exact-head Required CI/Gate, normal full quiesced
   rollout and independent installed/schedule/output verification for a fix.

The [supported cold-reversal exercise](https://github.com/colophon-group/jobseek/actions/runs/41213487268)
is dated 1137 evidence, not a new 1153/11500 exercise. Preserve supported rollback
assets through affected normal schedule observation and retire them through
supported GC. Follow [ADR-006](adr/006-crawler-deploy-quiescence-and-rollback.md);
no live rsync, mutable image deployment, partial writer restart or hold bypass.
Keep one current checkpoint, active regression inputs and supported rollback
assets. Remove owned temporary binaries/caches/raw history; preserve shared
dependencies, active corpus, other contributors' files and `dump.rdb`.
