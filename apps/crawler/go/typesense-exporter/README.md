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
retains the Python backfill for a cold rollback. Reconciliation and taxonomy
verification still use Python and remain part of the migration backlog.

`go test -race ./...` covers projection, acknowledgement handling, scan failure,
replay, cancellation, and cursor monotonicity. Set
`GO_TYPESENSE_TEST_DATABASE_URL` to an isolated PostgreSQL instance to include
the real SQL/fence/import/restart fixture; CI always runs that fixture.

## Dead-letter inspection

`go-typesense-exporter --inspect-deadletters` reads both Redis dead-letter
lanes, joins monitor IDs to a read-only repeatable-read PostgreSQL snapshot,
and checks the current Redis board hashes. It emits the same complete JSON
report as `crawler deadletters inspect`, which now execs this command before
opening Python pools. Configuration uses `LOCAL_DATABASE_URL` and `REDIS_URL`;
no Typesense credential or exporter ownership flag is required.

The shared Python classifier adapter invokes the same Go process for worker
lifecycle gauges, post-commit config sync, and retry/prune preflight. It has a
50-second timeout and drains/reaps the child on cancellation. The Go operation
has a 45-second deadline and batches database/config reads at 1,000 IDs.
Descriptors are read with one ZRANGE per lane to avoid pagination skips during
concurrent reaping. The join is observational, not a cross-store transaction.

Explicit retry/prune queue mutations remain in Python for this slice. The Go
inspector never retries, prunes, reschedules, changes due times, or clears
poison evidence. Read errors fail inspection; there is no Python fallback.
`testdata/deadletter_fixture.json` is the retained Python oracle; regenerate
from `apps/crawler` with `PYTHONPATH=. python
go/typesense-exporter/testdata/generate_deadletter_fixture.py`.
