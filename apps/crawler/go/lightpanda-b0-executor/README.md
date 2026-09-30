# Native Lightpanda B0 executor — inactive implementation

This module is the database foundation for the next full migration boundary.
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

Remaining work: strict private-socket admission and route preflight; canonical
task/runtime-v1 validation and authorization deadlines; current board/parser
identity checks; direct reusable Go parsing/enrichment; native taxonomy/index
loading/backfill within this pool; reserved-content policy and classification;
complete crash/lease-loss/reversal tests; Docker/CI integration and an inactive
candidate checkpoint. Follow the [current migration checkpoint](../../../../docs/28-go-location-resolver-checkpoint-2026-09-30.md).
