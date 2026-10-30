# Go and Lightpanda migration delivery plan

## Current checkpoint — October 30, 2026

Production runs **0.13.1124**, source
`001973db8fa29fac62ef8f1ffefd72d43ea3d9f6`, after the original complete
[deployment and promotion](https://github.com/colophon-group/jobseek/actions/runs/40976485216).
Both crawler image repositories reference the native digest
`sha256:bd026333822ae48590c8d129104d1130bd67d756e52c51c452d95f5c4c2c01e4`.
The runtime contract is
`5ced99a34dc1cf2bc46cc48a628d4ef8cdd8eb085b37e52c00d829f173255c72`;
schema is 0041, default ownership is Go at epoch 404, with no active migration
plans. Four native actors run and seven legacy processors are stopped.
The installed image has no Python, uv, Playwright or Chromium command.

The full migration goal remains **active**. Native stability and coverage passed,
but the acceptance readback found an R2 signature defect. The **0.13.1125**
correction below must pass required CI, the original complete deployment and
natural persistence verification before the service exit can be accepted.

The work continues from the merged
[salary migration #10177](https://github.com/colophon-group/jobseek/pull/10177)
and [delivery plan #10185](https://github.com/colophon-group/jobseek/pull/10185).
All timestamps below are UTC. Counts describe bounded observations, rather than
a permanently frozen production catalogue.

## Verified native execution and coverage

Independent readback verifies immutable runtime/image/data identities, healthy
native actors, stopped legacy actors and their compiled default-mode authority.
A complete ten-sample observation from **00:19:56 to 00:29:32** retained fixed
crawler, renderer and storage process identities; all four native actors had
zero restarts and no OOM. No jobs were forced and no limits were raised.

At the initial 8,143-board data generation, the installed-source SDK passed:

- **8,143 monitor and 8,143 detail configurations**, including canonical/cache
  identity, profile admission, configured worker lane and route metadata.
- **2,051,600 actual scheduled detail URLs**, with zero snapshot queue gaps or
  pending lane normalizations; fallback steps and intervals remained valid.
- Complete monitor routes with no wrong, unknown or malformed entries, and
  complete detail routes with no missing caches or wrong domains.

The complete queue/inflight boundary has **3,493,081 identities**. All **121**
removals from the preceding complete boundary are explained: 93 inactive or
disabled, 17 unscheduled and 11 currently queued or inflight. All 11 apparent
monitor gaps are currently queued or inflight. Of 3,059 retained wrong-worker
entries, 608 are inactive/disabled and 2,451 unscheduled. There are no missing
canonical records or unexplained eligible removals. All fifteen reconstructed
scheduled details retain their current expected queue/inflight projection;
the separate retired case is unscheduled.

The 1124 correction refuses disabled/nonrecoverable boards under the existing
canonical lock before default claim activation. The original raw attempt remains
available to bounded lease recovery; it cannot replace an existing productive
fence or write content. Enabled recoverable states retain their original route.
The previously fatal disabled case remains inactive, disabled and unscheduled,
with no live SQL lease, zero scrape failures and its pre-1124 receipt timestamp.

A subsequent supported data publication advanced the catalogue to **8,144**
enabled boards, data revision
`68eed98b311e04baa0c1f1e9b0a9293c9e19ecdb`, contract
`4fa0deddde9751c63bbab5712c33ab24b5bbed76808f21e35f4ad8c3f01aa339`.
The source/image remained unchanged. Follow-up checks must bind that publication
or its verified successor explicitly. Do not silently apply the 8,143-board
coverage result to a new catalogue; qualify the added profiles and scheduled
URLs, or perform the next complete fleet check after 1125 deployment.

## Persistence correction — 0.13.1125

The natural window exported **6,413** documents to Typesense and uploaded **229**
descriptions. It also scheduled **572** upload retries. Bounded native drain logs
identify HTTP 403 only for locale values with special characters; plain locale
codes upload successfully. The existing Go test checked object bytes, headers
and the presence of an Authorization header, without verifying the signature.

An independent local S3 HMAC verifier reproduces the failure for spaces, Unicode,
percent signs, plus signs, slashes and brackets. The generic Go SigV4 signer
adds an escaping pass that S3 does not use. The correction selects S3 canonical
URI signing and matches the existing Python `quote(key, safe='/')` object layout,
including literal slashes and encoded plus signs. It changes neither credentials,
canonical keys, description hashes, retry budgets nor upload deadlines.
Regression coverage includes ordinary and empty locale values as controls.

Production proof must show existing eligible retries uploading naturally and
canonical description/hash settlement through the original drain transaction.
Do not manually PUT objects, reset failure counters, force jobs or clear circuits.
Completed SQL fences include failed/skipped attempts; do not label every completed
fence a successful content sample. Likewise `last_enriched_at` and
`to_be_enriched` describe the separate offline enrichment workflow, rather than
proving that shared Go parsing, salary or location extraction ran.

## Freshness and measured resources

During the stable native window, successful monitors in the preceding hour grew
from **1,620 to 1,805**, and due monitors declined from **6,094 to 5,913**. Monitors
overdue by an hour grew from 4,690 to 4,711. All twelve monitors overdue by a day
retain their expected queue/inflight projection. This proves preserved work and
natural progress; it does not establish a zero-overdue freshness SLO. Publisher
failures, host circuits, pacing and retry backlog remain part of acceptance.

The qualified legacy control and this native window were recorded at different
times, with different task mixes and backlog. Whole-lane figures include crawler,
renderer, PostgreSQL and Typesense containers, including shared storage workload.

| Observation | Legacy control | Native 1124 |
| --- | ---: | ---: |
| Duration, seconds | 566.8 | 576.5 |
| Successful monitors / details | 726 / 1,454 | 625 / 214 |
| Mean whole-lane CPU cores | 5.43 | 3.31 |
| Mean whole-lane working memory, GiB | 11.97 | 11.35 |
| Successful tasks per CPU second | 0.709 | 0.439 |
| Modeled compute EUR per 1,000 successes | 0.001881 | 0.004325 |

There is **no accepted cost or throughput win**. The R2 defect and workload mix
limit this comparison. Repeat the native window after correction, preserving
fixed identities, zero restarts and aligned roles. Report monitor/detail counts
separately. The allocation model uses net hourly catalogue price multiplied by
max(CPU/host cores, working memory/host RAM), capped at host capacity. It is not
billed savings. Provisioned node sizes and their combined EUR 0.0624/hour catalogue compute
price remain unchanged.

## Production boundary and supported recovery

The production workflow builds the Dockerfile's `native` target and explicitly
selects the Go backend. Processing services are `ordinary-go`, `lease-reaper`,
`exporter` and `drain`. Go owns queue/fence/scheduling/persistence, enrichment
helpers, registry sync, migrations, maintenance, inventory and document
conversion/OCR. Browser work uses self-hosted Lightpanda; other proven routes
use Go HTTP/API. Production has no per-task Python or Chromium fallback.

Useful Python company setup, labelling, research, tests, offline enrichment and
operator tooling remain isolated from crawler processing. Legacy Docker targets,
Compose definitions and compatibility code remain for offline parity and the
required cold-reversal contract. Their presence does not authorize starting
legacy processors alongside the Go owner. Avoid speculative removal that breaks
these retained uses.

The original complete
[Go-to-legacy cold reversal](https://github.com/colophon-group/jobseek/actions/runs/40899557014)
succeeded, with independent image/spec/data/actor readback and no mixed ownership.
Retain the allocator; do not rewind epoch 404. Use the original full workflow and
[ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable identities,
mutation locks, complete writer quiescence and authenticated restoration.
Preserve the active generation, journal references, explicit rollback target,
five newest verified v3 generations and 48-hour staging grace. A failed restore
keeps writers stopped. No partial restarts, manual SQL/Redis/environment repair,
mutable tags or live rsync.

## Latest Lightpanda

Installed **1.1.0-nightly.11275+78bbb64db**, upstream commit
`78bbb64dbe1a3e1df8d2299cbf05efcff81bd65f`, still matches the latest official
[Linux release assets](https://github.com/lightpanda-io/browser/releases/tag/nightly)
checked October 30. Immutable renderer image:
`ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:4361f406e77fc0bd2fab56533e0bab51b56773e2c398cdbd78fd1e7b7c707805`.
Retain the reviewed memory, swap, concurrency, child-heap and response limits.

## Remaining delivery sequence

1. Qualify 1125 through focused signature/store tests, required exact-head CI,
   fresh merge authority and the original complete deployment with promotion.
2. Rebind verification to the actual installed source/image/data generation.
   Qualify current enabled profiles/URLs and complete queue conservation; verify
   canonical content, publisher reservations, natural retry/upload settlement,
   exporter/drain/reaper behavior and freshness without forced work.
3. Repeat aligned whole-lane measurements; report their task-mix/time/shared-load
   limits. Update this portable checkpoint with accepted outcomes and retained
   recovery/offline boundaries. Complete the goal only after the required
   production outcomes are proved.

## Storage and continuation

Keep one current checkpoint and conservation boundary, current qualification
inputs, and required operational rollback inputs. Delete consumed raw captures,
superseded copied scripts, test databases, compiled binaries, unused environments
and build caches after each batch. Historical migration evidence need not be
preserved. Protect other contributors' work and production generation retention.
