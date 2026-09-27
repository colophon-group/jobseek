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

## Collection schema setup

`go-typesense-exporter --setup-schemas` owns idempotent collection and alias
setup. Deployment invokes Go directly in the same credential-scoped,
provenance-labelled maintenance container while writers remain quiesced.
`crawler setup-typesense` and the developer wrapper exec the same binary.
Only Typesense credentials are required; setup opens no database connections.

The embedded seven-collection schema contract is checked against the retained
Python definitions in CI, including token separators and indexed symbols.
Existing alias targets are preserved. Missing fields are added; index drift
is repaired one existing field per PATCH. Type drift is reported without an
automatic type rebuild. Implicit `id` is never patched.

Synchronous schema requests retain the one-hour HTTP timeout. Each collection
repair has a two-hour deadline. Busy/ambiguous timeout responses trigger
schema-change observation (or bounded backoff when that endpoint is absent),
then a fresh schema read before any retry. Signals cancel requests and waits.
Optional allocator metrics retain before/after/delta evidence.

`--force` retains the existing explicit destructive operator operation: drop
the alias and its conventional `_v1` collection, then recreate. Deployment does
not pass it. Production proof is the ordinary successful setup phase of the
reviewed crawler deployment, followed by exact taxonomy verification. The
previous image/deployment script remains the cold rollback route.
