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
retains the Python backfill for a cold rollback. Reconciliation is migrated in the preceding release; the taxonomy verifier
below replaces the remaining Python step in the protected proof chain.

`go test -race ./...` covers projection, acknowledgement handling, scan failure,
replay, cancellation, and cursor monotonicity. Set
`GO_TYPESENSE_TEST_DATABASE_URL` to an isolated PostgreSQL instance to include
the real SQL/fence/import/restart fixture; CI always runs that fixture.

## Exact taxonomy verification

`go-typesense-exporter --verify-taxonomies` replaces the Python readiness
runtime; `crawler verify-typesense-taxonomies` execs it before opening Python
pools. Protected maintenance invokes Go directly. It is read-only and does
not acquire or move posting cursors.

- Reads location, occupation, seniority, technology, and company industry
  contracts from one PostgreSQL repeatable-read, read-only snapshot.
- Preserves localized display names, wildcard aliases, macro memberships,
  geographic ancestry, optional-field presence, and Python evidence hashes.
- Checks six live collection schemas and every authoritative document using
  searches of at most 250 documents. Dynamic posting counts stay excluded.
- Retains remote IDs and SHA-256 hashes, with at most 20 redacted mismatch
  details. Count drift, incomplete/repeating pagination, invalid UTF-8/JSON,
  oversized responses, duplicate authority, and unsafe IDs fail closed.
- Emits one JSON evidence record and returns nonzero for any mismatch or
  unverifiable state. SIGTERM/SIGINT cancels database/HTTP work and exits 130.

`testdata/generate_taxonomy_fixture.py --check` compares the embedded SQL/schema
contract and Go evidence fixtures to the retained Python implementation. It
runs only in tests. The Go PostgreSQL test executes all nine SQL queries and
proves snapshot isolation across a concurrent committed taxonomy change.
Deploy after Go reconciliation (#10072), then use the exact-revision
`verify-typesense-taxonomies` maintenance dispatch for production evidence.
Configuration/taxonomy sync and schema setup remain separate Python owners.
