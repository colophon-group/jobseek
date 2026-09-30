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
any subsequent mutation.

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
Docker build runs native unit contracts and installs the binary. Remote outcomes
are pending; cross-compilation alone does not establish Linux execution,
installed health, resource fit or production write ownership.

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
