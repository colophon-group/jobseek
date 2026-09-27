# Go Typesense runtime

The posting CDC exporter runs with `--run`; `--refresh-counts` refreshes the
five taxonomy/company count collections. PostgreSQL owns the posting cursor
and exporter owner. `--owner` is read-only; ownership transfers use the
explicit `--transfer-owner` compare-and-swap under the host mutation lock.

## Full posting backfill

`go-typesense-exporter --backfill` replaces the Python full reindex scan.
`crawler backfill-typesense` execs this command, preserving its exit status
and signal handling. The maintenance workflow invokes Go directly, followed
by the existing full reconciliation and taxonomy verification steps.

- Uses the same PostgreSQL posting/company query, projection, and Typesense
  acknowledgement parser as Go CDC. Active and inactive postings are included.
- Holds the shared PostgreSQL exporter fence from safe-cutoff capture through
  the final cursor save. It does not change the durable exporter owner.
- Scans `(updated_at, id)` from the beginning below one fixed commit-safe
  cutoff, in batches of 2,000 (`EXPORT_BATCH_LIMIT` may select 1–2,000).
- Refreshes taxonomy maps every ten minutes. Upserts are idempotent; each batch
  retries transport, protocol, and per-document failures up to five times with
  2/4/8/16-second waits. Persistent rejection fails the run.
- Saves the cursor only after the complete scan is acknowledged. Cancellation
  or failure leaves the prior cursor available for CDC/retry; a successful
  scan never rewinds it. Empty initial indexes do not create a synthetic cursor.
- Handles SIGTERM/SIGINT and has a four-hour process budget. The scheduled
  maintenance wrapper also bounds the full operation and retains its normal
  host lock, credential scoping, revision check, and non-overlap checks.
- Preserves `cron.start`/`cron.complete` events and optional Pushgateway
  completion gauges when `CRAWLER_PUSHGATEWAY_URL` is configured.

Run the existing `backfill-typesense` maintenance dispatch for production,
using its exact deployed revision guard. Do not run it inside the live
exporter's memory-limited container. A failed backfill can be retried from
the beginning; never manually move the cursor. The previous released image
retains the Python backfill for a cold rollback. Taxonomy verification still
uses Python and remains part of the migration backlog.

`go test -race ./...` covers projection, acknowledgement handling, scan failure,
replay, cancellation, and cursor monotonicity. Set
`GO_TYPESENSE_TEST_DATABASE_URL` to an isolated PostgreSQL instance to include
the real SQL/fence/import/restart fixture; CI always runs that fixture.

## Resumable Typesense reconciliation

`go-typesense-exporter --reconcile` compares a bounded UUID partition slice;
`--repair` applies and verifies repairs, `--full` completes the remaining
cycle, and `--repair --full --fresh-cycle` establishes a new complete proof.
`--max-partitions` defaults to 16; read-only scans also accept
`--start-partition`. Only `--target typesense` is accepted.

- Retains the existing PostgreSQL reconciliation lock, 256-partition state,
  run ledger, exporter cursor fence, and interrupted-run recovery. It never
  advances the posting CDC cursor or changes its owner.
- Compares IDs, active state, and the existing user-visible payload fields.
  Signed 64-bit candidate-order words remain exact. Positional arrays retain
  their order; locale and occupation ancestor arrays are compared as multisets.
- Streams UTF-8 JSONL with a 1 MiB record bound. Transient or truncated exports
  retry from fresh attempt-local state. No partial export reaches a repair or
  deletion consumer. Unbucketed candidates retain the 50,000-ID/16 MiB limits.
- Repairs frozen candidates under the exporter fence, rereads local rows,
  verifies index output, and proves source stability. It allows two candidate
  reads and three partition attempts before retaining the failed checkpoint.
  Deletes are bounded to 20 concurrent requests and finish before fence release.
- Final legacy bucket cleanup also holds the exporter fence, refuses to delete
  any candidate present locally, and requires a second complete export before
  setting the durable bootstrap flag.
- `--candidate-order-benchmark-sha256` retains the readiness receipt format and
  rereads the successful durable ledger before emitting it. SIGTERM/SIGINT
  records an interrupted run and exits nonzero; wrappers retain their existing
  time, resource, credential, and host-lock limits.

The full-backfill maintenance chain invokes Go reconciliation directly.
`crawler reconcile` execs Go before opening any Python pools. The existing
systemd wrapper uses that compatibility command in this first deployment.
Its separately attested host wrapper must switch to direct Go invocation in a
follow-up after this binary is deployed; do not install a wrapper that invokes
an unavailable command in the previous image. The previous released image
retains Python reconciliation for cold reversal. The Python library remains
available for offline parity checks until production proof and the rollback
window are complete.

Integration tests execute the repository's actual reconciliation state
migrations against isolated PostgreSQL schemas. They cover a lost import
acknowledgement, unchanged failed checkpoint, resumed repair, full 256-partition
proof, orphan/bootstrap deletion, durable readiness receipts, competing
owners, cancellation, and rejected concurrent partition advancement.
