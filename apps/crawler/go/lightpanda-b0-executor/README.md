# Native Lightpanda B0 executor — inactive implementation

This module contains the database and conversation foundations for the next
full migration boundary.
It is not installed in the crawler image or selected by the production overlay.
The production executor remains `src.lightpanda.executor` until the complete
conversation, parsing/enrichment, whole-lane admission and cold reversal pass.

`Store` uses one PostgreSQL connection, the same owner application name,
statement/idle-transaction/keepalive guards and existing write-fence functions.
Activation remains a separate short transaction. Authoritative posting,
description and schedule effects share require/write/revoke in one transaction.
The supervisor must hold its Redis lease mutex through commit acknowledgement;
this module has no Redis or renderer credential consumer.

The SQL comes directly from the existing Python detail/enrichment pipeline.
Exact uploaded HTML bytes determine the signed SHA-256-prefix hash. PostgreSQL
deduplicates descriptions by their bytes, retaining equal-body completed uploads
and timestamps. Nullable field slices preserve SQL NULL/COALESCE semantics.
Ordinary detail and enrichment saves retain their different missing-row behavior.
Transient, budget and permanent-gone paths preserve visibility and scheduling;
the `never` rescrape policy remains database-owned.

Run real transaction tests only against an isolated migrated database whose
name ends with `_b0_executor_test`:

```sh
JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL=postgresql://fixture@127.0.0.1:54393/jobseek_b0_executor_test \
  go test -race ./...
go vet ./...
go mod tidy -diff
```

The initial local tests used PostgreSQL 18.6 with all repository migrations.
They supplement, rather than replace, PostgreSQL 17 CI and the production
operational contract. They prove rollback/cancellation, stale epochs, duplicate
claims after commit, exact-description deduplication, native SQL field codecs,
failure budgets and never-rescrape scheduling. The frozen hash fixture comes
from Python 3.13's signed big-endian first-eight-byte SHA-256 calculation.

The transport foundation reuses runtime-v1 framing and protobuf types. Frozen
Python messages match byte for byte; request bounds, advancing authorization,
route identity, canonical base64 and recursively unknown protobuf fields are
checked. Frozen rendered-result cases preserve complete inline chunk integrity,
strict UTF-8, empty HTML, chunk boundaries and HTTP status classification. These
helpers and the private socket server are not installed or production owners.

The server preserves four task conversations plus a reserved route attestation.
It checks private directory/socket ownership and Linux same-UID peer credentials,
requires the PostgreSQL epoch at startup and health admission, bounds slow first
frames and gives inflight commits a 15-second shutdown grace. Socket removal is
bound to the owned inode. Local race tests exercise admission, slow/wrong peers,
stale epoch, authority-loss responses, successful commit during shutdown and
cancellation at grace expiry. Linux credentials are cross-compiled locally;
the real SO_PEERCRED test still requires Linux CI execution.

`contracts/v1/b0task` factors the existing Go producer/supervisor identity codec
without queue, database or parser dependencies. The existing supervisor regression
suite still passes. Native executor admission additionally reconstructs the
complete canonical task, including required zero/null fields, and rejects changed
route/owner, whitespace in source URLs and invalid claims before authorization.
Frozen Python JSON-LD/DOM/Unicode task identities match. Expanded JSON number
and string oracles correct Python float exponent thresholds and DEL escaping.
The shared codec changes therefore require fresh runtime admission before use.

Native location loading now streams the same PostgreSQL core rows through
this pool into a private 0700-directory/0600-file SQLite index. It preserves
name variants/deduplication, English display preference, mutable lookup
serialization, 500-key non-core backfill and the monotone negative cache.
Real isolated PostgreSQL tests pass for core matching, Japanese backfill,
549 negative keys, the single-connection budget and owned index cleanup.
B0 still discards taxonomy-miss telemetry. No production loader is replaced yet.

Current PostgreSQL posting/board/parser reads are implemented and tested for
source, board/browser enablement, parser revision, missing type and interval
drift. Deleted/unscheduled postings preserve the existing skip behavior. Scalar,
location-array, HTML title, locale, garbage-title and employment-type helpers
match frozen Python cases; they are not yet a complete persistence pipeline.

Remaining work: the complete task authorization/commit handler and startup
environment guards; direct reusable Go parsing/enrichment; remaining taxonomy
ID/rate loading and enrichment assembly; reserved-content policy and classification;
complete crash/lease-loss/reversal tests; Docker/CI integration and an inactive
candidate checkpoint. Follow the [current migration checkpoint](../../../../docs/28-go-location-resolver-checkpoint-2026-09-30.md).
