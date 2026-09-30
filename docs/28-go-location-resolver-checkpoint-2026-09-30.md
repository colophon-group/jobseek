# Go location resolver and salary continuation checkpoint

Continuation of the [full migration plan](27-go-lightpanda-continuation-plan.md).
The full-delivery goal remains active. This checkpoint records an ordinary-worker
deployed location matching and salary slices, rather than completion
of the crawler service migration.

## Salary rollout

[PR #10177](https://github.com/colophon-group/jobseek/pull/10177) was merged on
2026-09-30 at 10:11:53 UTC as `3fe58bcaf639f3b666cb283627dd1e5bb5eda80f`,
v0.13.900. Its exact checked head was
`56d80cdf724d436be35fc11b3252cbe7f6c5fe09`, against main
`9f068eb2352867bc0ed6b9835f23b7339d28c6a2`. Required CI, Crawler Deploy Gate
and installed-image parity passed. Concurrent Codex service/CLI/smoke changes
were incorporated before the bound merge.

[Deployment 36700930614](https://github.com/colophon-group/jobseek/actions/runs/36700930614)
completed successfully, including full promotion. The committed crawler image is
`ghcr.io/colophon-group/jobseek-crawler@sha256:cb16b8bc995b50ac04ca3937758323a2db28cf3df35edce19200427686d21bd6`;
the browser image is
`ghcr.io/colophon-group/jobseek-crawler-browser@sha256:63ece6fc93f18ef482f42fb10967880d2367197e27e812f68eadc9f4c53f8982`.
Both carry revision `3fe58bcaf639f3b666cb283627dd1e5bb5eda80f`.

The supported cold rollback retired cdom epoch 135 at epoch 136: 21 schedules
restored, two terminal tasks dropped, no write fences remaining, and the
ordinary writer set healthy. All 25 selectors were cleared with the committed
helper, under the mutation lock, against promoted revision
`20b4031ccc7e3056c6ca10b55b68bd5562c0c720` (v0.13.898).
The prior concurrent v0.13.899 deploy stopped at the active-B0 guard before
quiescing writers. No hold or receipt was bypassed.

After promotion, the helper staged all 25 selectors under the mutation lock at
the full promoted revision, and the supported wrapper activated cdom epoch
**137**. The active receipt records plan digest
`b30a004da9879c6735a6954b28a754e3e34b0c9d740ff24223c5269a6badd10a`
and compose digest
`8410d762d3207eec6768ad05b22c2cfe54da4794e4e407cfa7cc4868f027fc80`.
Twenty-one selected schedules transferred; 15 had no queued task. The retained
cohort still had 140 postings, 40 active. At 10:35:19 UTC all workers and the B0
services were healthy; ordinary workers recorded 3,502 successful Go salary
operations and no salary errors. The snapshot did not yet show fresh B0 write
fences; ordinary enrichment counters do not establish native B0 execution.

A protected replay of 512 stored descriptions from 140 boards and 17 locales
matched Go and Python salary extraction/unification/EUR results exactly.
Input SHA-256: `215fd813e545558a454770f107b5b377abe029c8008e39215a12dedba6c653c8`;
output SHA-256: `23561799809393ad732132c137065335b76945d443cc76e253ee2c09e2b429c3`.
The subset with descriptions updated after 10:27 UTC contained 394 postings,
76 boards and 14 locales; 46 carried extracted salary. All five persisted salary
fields matched. Twenty-two older Dupont descriptions had null stored salary,
despite positive replay output; their content/scrape timestamps predated this
release. Posting `updated_at` alone is not evidence of fresh enrichment.

Before any later crawler or renderer rollout, re-read live receipts, release
identities and holds. Use supported cold rollback and the guarded selector
helper when applicable; never infer mutation authority from this snapshot.
Follow [the salary handoff](24-go-lightpanda-salary-checkpoint-2026-09-28.md)
for guarded commands with current authoritative identities. No forced due
times, publisher refetches or duplicate origin probes were used.

See [sanitized salary production evidence](evidence/go-salary-production-2026-09-30.json).

## Durable renderer rollout

[Repair #10192](https://github.com/colophon-group/jobseek/pull/10192) merged as
`b83bf7c9e9fb88f16e90fefb7a9f4234071ef8db`. The official September 30
multiarch source is pinned by immutable OCI index and checked binary digests,
so daily upstream asset rotation no longer breaks candidate builds. Both Linux
architectures passed integration, egress/identity isolation and density smoke;
[admission 36707930551](https://github.com/colophon-group/jobseek/actions/runs/36707930551)
passed all 16 fixture arms. This is synthetic admission, not production resource
proof or native executor completion.

The supported wrapper retired cdom at epoch **138**, restoring 21 schedules,
dropping no terminal tasks and leaving no write fences. Renderer deployment
36713362647 stopped before host mutation when main advanced. The intervening
change was web-only. After transient GitHub dispatch errors,
[deployment 36714124285](https://github.com/colophon-group/jobseek/actions/runs/36714124285)
completed promotion at exact source
`d04bb56b9938268e83b5b7452e04fec6a9edef2c`, renderer image
`ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:65fff3f9b08cc4db7010e8fe7ed4cd2fcc8bac6e7320f47dae8ed3ded7aa2b34`.
The host's public release identity matched its deployment artifact. The image
build checks ARM64 binary SHA-256
`112d39b5020a80e2de6b828485880eb7b8271e42c5598b825a760a8688fe0b21`.

The unchanged 25 selectors and promoted salary crawler revision were retained.
Supported reactivation completed at cdom epoch **139**, selecting 21 schedules,
15 without queued tasks. At 12:27:37 UTC all ordinary and B0 workers were healthy
and all seven metric collectors responded. This initial snapshot had no newly
claimed B0 task yet. At 12:55:35 UTC, epoch 139 recorded one natural successful
render, one committed executor operation and one accepted reschedule, with no
render/executor failures. The database executor still ran Python. This metrics
observation does not establish canonical database parity or native ownership. See
[sanitized renderer production evidence](evidence/go-lightpanda-renderer-production-2026-09-30.json).

## Location rollout v0.13.901

`apps/crawler/go/job-enrichment` now has a reusable SQLite-backed Go location
resolver and a bounded `location-resolver` JSONL companion. Public resolution,
display names and ancestor traversal dispatch through it when the enrichment
engine is Go. Errors propagate; there is no per-task Python fallback.

The parent loader still uses the existing PostgreSQL pool and unchanged SQL,
including 500-key non-core-name backfill chunks. It populates a private disk
index instead of retaining the complete SQLite database in Python memory.
Go opens that index read-only and keeps only per-operation entry caches.
Forks do not signal the parent's resident or delete its index; process restarts
rehydrate the same file and negative cache. Dependency notices are bundled in
the image. The native worker/executor migration must replace the Python loader
and backfill owner before Python retirement.

Evidence prepared locally:

- 322 frozen resolutions from 329 passing Python regression tests; matching
  leaf IDs, location types/order, lookup misses and raw miss samples.
- The existing 329-test suite also passed through the native public resolver.
- 248 frozen CPython integer-set cases preserve equal-context ranking ties;
  Unicode digit classes, accent/name variants and regex boundaries retain the
  existing rules.
- 368 focused Python tests passed, including six location lifecycle/ownership
  tests and real load/backfill using one pool acquisition at a time and
  500-key chunks.
- Go race, vet, module and format checks passed. Required CI and installed-image
  parity passed on draft head `3859cee2af9bc4687ca866c2716f4ded9c308d8d`.
  A changed head/base requires fresh checks before merge.
- A read-only repeatable production taxonomy snapshot contained 37,526 entries,
  143,004 core names and a 7,925,760-byte SQLite index. Go matched 1,024
  resolution cases and 128 display/ancestor cases exactly, with zero drift.
  Index SHA-256:
  `610a9cfc22654666d3a00627f916411caabc6315fbe47255ef98088a2b36648a`.

The private snapshot, inputs and replay evidence live outside Git in
`/Users/Viktor/.codex/migration-evidence/go-salary/2026-09-30/` (protected local
evidence). A future operator can recreate the replay from the read-only
production taxonomy; local paths alone are not deployment authority.
This is compatibility evidence; whole-lane production resource comparison remains
required. The matching companion is now deployed as recorded below.

## Location production promotion and restoration

[PR #10189](https://github.com/colophon-group/jobseek/pull/10189) merged at
13:09:38 UTC as `ec906f05cb5c481ea988c9d8dd5ffdac29ee1454`, v0.13.901.
The checked head was `089dc82b27a11856ca7c6bc22ec0c7b5b2eb706b`, against
`d04bb56b9938268e83b5b7452e04fec6a9edef2c`. Required CI 36715107576,
installed runtime parity 36715107562, fixture whole-lane admission 36715107504
and final Crawler Deploy Gate 36718992804 passed. Main advanced with web/MCP
changes; fresh authority reviewed target `fb1cf6fb2f13e7e15837e242c4e9255e71011dfa`
and verified that crawler, renderer, deployment and agent inputs were unchanged.

The supported cold wrapper retired epoch 139 at **140**, restoring 21 schedules,
dropping none and leaving no fences. The guarded helper cleared exactly 25
selectors under the mutation lock at the then-promoted salary revision.
[Full deployment 36719644632](https://github.com/colophon-group/jobseek/actions/runs/36719644632)
succeeded including promotion at 13:23:31 UTC. Host readback confirmed:

- Crawler: `ghcr.io/colophon-group/jobseek-crawler@sha256:943c0a36353da52e83908299aa61ccbf6a425f8f58048ee1720bc46f3ec1fc4d`.
- Browser: `ghcr.io/colophon-group/jobseek-crawler-browser@sha256:b9560dd608efcf673430e2b6a8138e33dd6a31d1e544f4732392ba3458338924`.
- Revision: `ec906f05cb5c481ea988c9d8dd5ffdac29ee1454`, v0.13.901.

After exact-revision readback, all 25 selectors were staged under the lock and
the supported wrapper restored cdom at **141**. It selected 21 schedules, 15
without queued tasks; all workers/B0 services became healthy and all seven
metrics collectors responded. Plan digest:
`1d07252aade2ed6f94fd807a517010481f7a98573446d642ce806757aa1514d7`;
compose digest:
`5270b210b5e3364b2391fd712ff3ff4ead9b88e190de550c01bb0f4e202c47c4`.

At 13:39:03 UTC natural ordinary traffic recorded **80,641** successful Go
location resolutions and no capability errors. A read-only repeatable database
snapshot at 13:43:16 UTC retained 140 cohort postings, 39 active, with no residual
write fences. No cohort scrape was fresh since activation, and the B0 counters
had no fresh render/commit yet. Canonical fresh location IDs/type arrays and
miss/backfill output therefore remain pending natural output verification.
Do not infer parity from health, a vacuous empty fresh subset or counters alone.
No publisher refetch, forced due time or manual writer restart was used.

See [portable location production evidence](evidence/go-location-production-2026-09-30.json).

## Remaining runtime ownership and next delivery

Ordinary workers and the browser worker select `JOB_ENRICHMENT_ENGINE=go`.
The hardened B0 DB executor overlay currently does **not** select that engine,
so its shared enrichment remains Python by default. Do not describe Go counters
from ordinary workers as evidence of an entirely native B0 lane. Its replacement
is the next major delivery boundary.

1. Renderer build pin repair and supported cold rollout are complete as recorded
   above. Fresh location admission passed against the repaired immutable pin.
2. Location promotion and supported cohort restoration are complete. Verify
   natural canonical IDs/type arrays, misses/backfill and freshness as output
   arrives. Re-read live ownership before any subsequent rollout.
3. Replace the B0 DB executor with Go using the existing fenced transaction and
   authorization/commit protocol. Move taxonomy/index loading and backfill onto
   its existing bounded connection budget. Prove lease loss, crash, stale epoch,
   transaction rollback and cold reversal before changing production ownership.
4. Introduce native ordinary workers for already ported HTTP/API profiles, then
   reconcile the entire enabled fleet and remaining runtime consumers. The
   current read-only effective census has 7,884 enabled boards, 102 monitor
   values, 205 effective profiles, 17 implicit scraper rows and 523 boards with
   any browser requirement. These are coverage denominators, not native-owner
   counts; the Python orchestration boundary remains.
5. Complete comparable whole-lane CPU/RAM/density/cost measurement and final
   supported cutover/reversal. Retire production Python, Playwright and Chromium
   after replacement authority and the rollback window are established. Retain
   useful isolated offline tooling. Refresh and validate the official Lightpanda
   nightly separately through its supported pinned renderer deployment.

## Native executor foundation saved locally

The inactive module `apps/crawler/go/lightpanda-b0-executor` is being implemented
on isolated branch `fix-crawler/go-b0-native-executor`. Local commits
`747ce5ac763e899ae548f74bbe3f7cf173d5e24a` and
`2ebae3f52e0e60020537d006a66b45b951268097` preserve the database and protocol
foundations; they are not pushed, installed or production owners.

A one-connection pgx store preserves frozen Python SQL, fenced activation and
require/write/revoke transactions, nullable COALESCE fields, exact-description
hash/deduplication, schedule truth and failure budgets. Real transaction tests
passed with every repository migration on isolated PostgreSQL 18.6, including
cancellation/crash rollback, stale epochs, duplicate claims, native field codecs
and never-rescrape scheduling. PostgreSQL 17 CI remains required.

Transport helpers reuse runtime-v1 framing and types. Frozen Python messages
match byte for byte; authorization identity, frame/result bounds, canonical
base64 and recursively unknown protobuf fields are checked. Rendered-output
fixtures cover chunk integrity/partitioning, strict UTF-8, empty HTML and HTTP
classification. Race, vet, module and format checks passed. A complete private
socket server, canonical task identity, direct parsing/enrichment, native
location loading, Docker integration and operational fault tests remain before
an inactive candidate can be reviewed. This implementation is separate from the
currently selected Python executor and is not evidence of native B0 ownership.

## Native executor implementation contract

The next implementation should replace `src.lightpanda.executor` behind its
existing private Unix socket, not create another queue or database authority.
Keep the one-connection PostgreSQL pool, four task conversations plus one
reserved route-attestation conversation, same-UID peer checks, 0700 socket
directory and 0600 socket, bounded shutdown and health admission.

Preserve the exact `jobseek.lightpanda.executor/v1` conversation and canonical
Go-owned task/digest validation. Frames are bounded to 3 MiB, runtime-v1 results
to 2 MiB, strict UTF-8 HTML to 1 MiB and inline chunks to 64 KiB. Reject unknown
protobuf fields and unsupported result shapes. Keep first-frame, authorization
and commit deadlines; the supervisor owns the Redis lease mutex from
`authorized` through `committed`. The executor receives only database authority,
with no Redis, renderer credentials or origin HTTP client.

Port the existing short fence activation transaction and the separate
require/write/revoke transaction. Re-read current posting, board, parser
assignment, active state, interval and authoritative next due time. Reuse Go
DOM/JSON-LD parsing and enrichment packages directly, including native taxonomy
loading/backfill through the same connection budget. Preserve reserved-content
policy, empty/garbage-title classification, gone/transient/failure budgets,
COALESCE retention, scalar timestamps, exact-HTML description UPSERT and signed
SHA-256-prefix hashes, pending R2 state and success scheduling. The admitted
lane's no-fallback constraint must remain explicit.

Prove the replacement against frozen Python conversations and real PostgreSQL
transactions, including stale epoch/claim, lease loss, reserved tasks,
unschedulable/deleted postings, parser changes, timeout/cancellation, rollback,
crash after commit before acknowledgement, duplicate delivery, health under
four active tasks and cold reversal. Deliver an explicit inactive candidate
first; then require installed-image proof, whole-lane admission and natural
cohort DB/freshness evidence before changing production executor ownership.

Honor deployment holds, mutation locks and exact head/base merge authority.
Keep owner-closed #7966 closed; its retirement criteria still apply. The merged
[continuation plan](27-go-lightpanda-continuation-plan.md) remains the full goal's
sequence and completion contract.

## Natural B0 readback after location rollout

The 16:32:27 UTC read-only runtime snapshot retained the exact v0.13.901
release and active epoch 141, with healthy workers and three successful
B0 renders/commits, no render/executor failures, 21 ready and zero inflight.
At 16:34:10 UTC, canonical database readback found three naturally fresh
postings since activation (two Browser Use, one Bunq), active with zero
failures and parallel location ID/type arrays. Their location arrays, titles
and description hashes matched the 13:43 database snapshot; description
deduplication retained older equal-body rows. Three epoch-141 fence rows
were observed. No forced due times or publisher refetch were used.

[Portable natural evidence](evidence/go-location-natural-b0-2026-09-30.json)
binds the release, receipt, observation times and numeric comparison. Raw
current rendered location inputs were not retained for an independent replay.
The Go location path is deployed behind the Python loader/executor; this
readback does not establish native B0 or ordinary worker ownership.
