# Native Go B0 database executor candidate

Continuation of the [full migration plan](27-go-lightpanda-continuation-plan.md)
and [promoted location checkpoint](28-go-location-resolver-checkpoint-2026-09-30.md).
The full migration goal remains active. This candidate is implemented on
`fix-crawler/go-b0-native-executor`, version 0.13.902. Its Docker build installs
`go-lightpanda-b0-executor`; the production overlay still selects Python.
No native production ownership, installed-image proof or final migration
completion is claimed by this checkpoint.

## Current production boundary

Location PR #10189 and full deployment 36719644632 promoted v0.13.901 at
`ec906f05cb5c481ea988c9d8dd5ffdac29ee1454`. Supported cold retirement at epoch
140 and restoration at epoch 141 are recorded in the linked checkpoint.
The portable production evidence and updated checkpoint merged in
[PR #10202](https://github.com/colophon-group/jobseek/pull/10202) as
`1222fbe5d24a8cfd0591382ff771cb1b613b1192`.

At 14:07 UTC, all metric collectors responded, the host retained that exact
release and epoch 141, and no fresh B0 render/commit was observed. Ordinary Go
location calls are observed; canonical fresh B0 output remains pending natural
processing. Do not force due times or refetch publisher pages to manufacture
that evidence. Re-read current receipts, release identities and holds before
any subsequent mutation. A fresh read-only runtime collector at 15:41:59 UTC
confirmed the same immutable v0.13.901 release, epoch 141 and healthy ordinary
and B0 services; the native candidate remains unselected.

## Implemented candidate

The native module at `apps/crawler/go/lightpanda-b0-executor` preserves the
existing four-task/private-UDS conversation and reserved health slot. It has
no Redis or renderer authority credential consumer. It checks canonical task
identity and bounded runtime-v1 results before asking the supervisor for an
advancing write lease. The supervisor retains its lease mutex until commit
acknowledgement. Database activation remains a separate short transaction;
authoritative posting, description and schedule effects share require/write/
revoke in one transaction through one PostgreSQL connection.

It rechecks the mutable posting/board/parser assignment after activation.
Deleted, unscheduled and reserved content keep the existing skip behavior.
Verified rendered HTML feeds the existing Go DOM/JSON-LD packages directly,
with no origin request or parser subprocess. Gone redirects precede challenges;
HTTP 404/410, budget failures and transient failures retain distinct visibility
and scheduling behavior. The shared retry counter increments on transient
failures too; this migration preserves the frozen SQL rather than inferring
behavior from older comments.

Native taxonomy/rate loading uses the existing pool. Location core loading and
500-key backfill populate a private 0700-directory/0600-file SQLite index with
serialized mutable resolution. Scalar coercion, ordinary content and selected
enrichment preserve NULL/COALESCE, independent title matching, internship
overrides, monitor-field retention and empty-row backfill. Description bytes
retain the existing signed SHA-256-prefix hash, locale, deduplication and R2
pending-upload effects. Empty or malformed extraction remains transient.

The canonical task codec is shared with the Go producer/supervisor without
importing parser/database packages into those services. Frozen Python number
and string cases corrected float exponent thresholds and DEL escaping; this
shared runtime change requires fresh admission. The candidate bumps VERSION.

Startup validates exact mode, socket/shard/epoch, one-connection configuration
and forbidden authority credentials. The health command uses the reserved route
conversation, opening no additional database connection. Slow first-frame
classification is capped at 16 sockets. Normal shutdown gives admitted commits
15 seconds; a failed grace returns for process termination without waiting
indefinitely for resource cleanup. Installed fault proof remains required.

## Verification completed locally

- Frozen Python protocol, canonical JSON/task, rendered manifest, description
  hash and seven SQL-query checks pass.
- Both existing frozen DOM and JSON-LD parser corpora pass through the direct
  native rendered path, including parser errors and classification rules.
- Fourteen Python processing transaction cases pass for ordinary and selected
  enrichment, backfill, internship, language/NULL gates and empty results.
- The existing supervisor suite and shared contract package passed after codec
  extraction and canonical JSON corrections.
- Real isolated PostgreSQL 18.6 tests with all repository migrations pass for
  fence rejection, rollback/cancellation, duplicate claims, field codecs,
  description deduplication, failure/never-rescrape schedules, mutable identity,
  native location/rate loading and private index cleanup.
- Socket conversations against that database pass for native canonical writes
  and schedule acknowledgement, invalid authorization, revoked duplicates,
  failure classes, reserved content and unscheduled skips. Their processing
  fixtures do not replace a production taxonomy/whole-lane replay.
- Go race/vet/module/format checks, Linux ARM64 compilation and the offline
  Python oracle lint/format checks pass. CI workflow contract tests pass (91).

Required CI now includes a Linux PostgreSQL 17 native-executor job. The candidate
Docker build runs native unit contracts and installs the binary. At candidate
head `44660f03c5dd8af6132bcdfc4466ea4af3cf36e3`, Required CI run
36729181871 and installed runtime contracts run 36729181987 passed, including
real Linux PostgreSQL 17 native tests. Those installed contracts do not yet
exercise native process ownership. The installed-executable test passed
in CI at head `b47394e6d486e75cf743c95a3a85c8716729ad20`: native job
109946247296 in Required CI run 36732557873 passed, including the installed
executable step. It uses UID 10001, a 64-descriptor limit, real peer credentials, native
taxonomy startup, one PostgreSQL connection, four held tasks plus health, lost
acknowledgement followed by a hard kill/restart, duplicate rejection, stale
epoch rejection and bounded cleanup. It models a fresh private tmpfs on restart;
container cgroup and read-only filesystem proof remains separate. Its lost-ack
case withholds an already-generated acknowledgement from the supervisor; it
does not prove death before acknowledgement generation or Redis conservation.
The next candidate changes the synthetic ARM64 admission executor command and
health check to Go, retaining the same 384 MiB/one-CPU/read-only/private-tmpfs
container contract. Admission checks live executable identities, UID 10001 and
the 64-descriptor limit before and after collection, and rejects missing or
Python owner evidence. Native whole-lane run 36735074058 subsequently passed all 16 arms and eight
paired comparisons at PR head `1bae9e79528aaab97c1540bb536798f740d9549d`,
tested merge checkout `b0d5e99cdac7db30653aa4c1dc4e305e13dd44ca`.
[Portable synthetic evidence](evidence/go-native-b0-synthetic-2026-09-30.json)
records canonical hashes, exact queues/metrics/cleanup, live Go ownership,
container envelopes and numeric resources. The candidate had roughly
55–69 MiB sampled aggregate peaks; CPU and latency comparisons passed. These
are synthetic fixture results after startup, not production-taxonomy, startup
resource or attributable-cost proof. The subsequent policy/crash changes need
fresh admission.
These fixture results do not establish production write ownership.

## Fault and publisher-policy evidence

A stronger installed fixture is prepared after the passing lost-ack test. It
holds description persistence, queues an exclusive posting-table lock, then
releases persistence. The queued lock acquires after the native transaction
commits and blocks its post-commit schedule read. The fixture verifies durable
canonical content, observes the blocked read, and kills the real owner before
it can generate an acknowledgement. Its first Linux execution at head
`1bae9e79528aaab97c1540bb536798f740d9549d` failed at the description barrier:
the driver polls activity inside a transaction, which can retain a statistics
snapshot. The fixture now clears that snapshot before polling; its corrected
Linux execution passed in native job 109970327719 of Required CI run
36739532630 at PR head `3ba0da038cba239250052b7830b45ed6aee39f2a`.
The installed executable was killed after durable commit while its schedule
read was blocked, before acknowledgement generation. Restart rejected the same
claim and retained the committed schedule. This uses only isolated migrated
fixture tables, with no production fault hook or publisher request. It does
not replace Redis-linked fault or production recovery proof.

The held-result policy audit identified an existing B0 boundary gap in both
executors: `src/lightpanda/runtime.py` validates the HTML manifest and invokes
raw Go parsers without the shared header/meta reservation check.
Before this candidate, `BrowserSuccess` carried no main-document response
headers and the renderer discarded CDP response headers. The native direct
parser originally followed that path. Stored `tdm_reserved` skips alone did
not prove detection or persistence of a fresh resource signal.

The candidate implements that boundary with additive optional
`BrowserSuccess.resource_policy` signals. The renderer captures only bounded
TDM header values from its correlated main-frame/loader/URL snapshot. The native
executor requires signal-message presence before parsing; the fallback Python
path accepts the optional field and runs its existing shared check. An empty
present message means header inspection, and absence means unknown coverage.
The supervisor checks effective signals before a challenge retry.

A shared Go package matches 21 frozen Python header/meta cases, including
Unicode excerpt bounds, extra/duplicate attributes, entities, script/style/
comment literals, last-valid metadata and metadata precedence. Native
PostgreSQL tests cover fenced reservation persistence without title/description/
visibility changes, opt-in precedence, malformed/missing-evidence rejection,
duplicate rejection and schedule exclusion. Existing reserved/unscheduled
native skips now revoke their fence. A final locked reservation check prevents
content persistence when a reservation wins during processing.

Local validation passed real isolated PostgreSQL race tests, 75 Python B0
tests, generated-binding stability, contract/supervisor/renderer unit suites,
and cross-compiled Linux integration/vet. The native policy and corrected
installed crash tests also passed on Linux PostgreSQL 17 in job 109970327719.
The renderer image build initially omitted the new shared policy package from
its selective contract copy; the build context is corrected and compiles for
Linux ARM64. Actual renderer header correlation
and the complete new installed image/lane remain pending remote Linux evidence.
Strict deployed consumers reject unknown protobuf fields, so release the new
crawler and renderer through supported cold transitions before restoring B0;
there is no live backward-compatibility claim for an old consumer receiving
these signals. Before native ownership promotion, prove the exact candidate
across those installed boundaries. Preserve listing visibility, facts and
retained-description display, and enforce existing downstream mining gates.
Use synthetic entry-point cases, including signal-free, reserved, HTML opt-in,
malformed manifest, redirect correlation and rollback/revocation cases. Do not
claim complete origin-file/protocol coverage: the broader existing work remains
in issue #10090 and its documented scope. This audit authorizes no blanket
source exclusions or new origin traffic.

## Startup resource proof in progress

The corrected renderer image and pinned integration suites passed on ARM64 and
AMD64 at PR head `a990177d2363dae33c10e9553bcbcce683dfc36b`: renderer run
36740886647, pilot run 36740886649. Required CI run 36740886650 and installed
runtime contracts run 36740886648 also passed. Native whole-lane run
36740886561 is still measuring; retain its terminal artifact before replacing
that exact candidate.

A read-only production startup-taxonomy snapshot at 16:08:46 UTC contains
37,526 locations, 143,004 names in the six startup locales, 186 technologies,
91 occupations, nine seniorities and 31 currency rates. The complete raw
snapshot stays private. The optional isolated native test restored those rows
to local PostgreSQL 18.6 and loaded the real native lookups/index in 1,347 ms.
Its private index held 118,206 deduplicated name pairs and 37,526 English display
names in 7,806,976 bytes (7.45 MiB), kept one PostgreSQL connection, resolved
the fixed geographic case and cleaned up its owned files.
[Portable numeric evidence](evidence/go-native-b0-startup-taxonomy-2026-09-30.json)
binds the snapshot hash and scope. This local loader result excludes Linux
installed-image CPU/RAM, decoding/import overhead and transient tmpfs peaks.

The next admission change records cgroup CPU from container creation through
its final read and the prefix before post-startup sampling. It preserves
synchronized post-startup memory sampling for density and separately records
the sum of service lifetime peaks as a conservative aggregate upper bound.
It rejects missing/inconsistent lifetime counters, startup OOM/swap evidence
or an upper bound reaching the lane envelope. This sum is never represented
as a simultaneous peak or used as a density denominator.

Both disposable lanes also receive a generated startup taxonomy with the
observed production row counts before their due time is set. The fixture
census must match in every arm. Generated names/slugs are synthetic and do not
establish production-taxonomy semantic parity or production cost. Local real
PostgreSQL census, adversarial numeric admission checks and native snapshot
loading pass; exact Linux admission of these new checks remains pending.

## Next delivery gates

1. Complete installed-binary/image tests with real Linux UID isolation, private
   sockets, native taxonomy startup, the one-connection budget, four tasks plus
   health, read-only filesystem/tmpfs limits and bounded startup/shutdown.
2. Prove crash after commit before acknowledgement, lost/changed authorization,
   stale/current route changes, connection/CPU pressure and quiesced cold
   reversal. Audit publisher-policy/reserved-content coverage across the held
   renderer result and HTTP/browser routes without adding origin traffic.
3. Pass exact-candidate Required CI, installed runtime contracts, whole-lane
   admission and Crawler Deploy Gate. Keep production selection Python until
   all ownership gates pass. Refresh base/head authority and deployment holds
   immediately before any merge or mutation.
4. Select native B0 through the supported full release/cold wrapper, observe
   natural tasks and compare canonical fields, description/R2 effects,
   database-owned schedules, queue conservation and freshness. Measure the
   complete comparable lane, including startup and every consumer.
5. Continue native ordinary worker ownership, enabled-profile reconciliation,
   remaining runtime maintenance consumers and full resource/cutover/reversal
   proof. Retire production Python/Playwright/Chromium after replacement
   authority and the rollback window; retain useful isolated offline Python.
