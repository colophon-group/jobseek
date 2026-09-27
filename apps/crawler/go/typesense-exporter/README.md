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

## Post-commit taxonomy and company publication

`go-typesense-exporter --sync-taxonomies` owns the Typesense stage after the
Python CSV/local transaction commits. The parent waits for Go and propagates
failure; shutdown terminates and reaps the child. There is no Python runtime
fallback. CSV parsing/local writes, Redis board effects, and deadletter reporting
remain to migrate.

Go reads one static authority snapshot for all five collections, including
full localized company details. It preserves producer strings and unordered
alias semantics. All active/year counts use the existing Typesense facet
filters; local taxonomy count queries remain bootstrap fallbacks. This also
avoids the initial seniority/technology PostgreSQL posting scans during normal
operation: the final published values match the existing count-refresh stage.
A final Go count refresh and typeahead invalidation preserve freshness.

Imports have at most 1,000 documents. Company publication requires every
acknowledgement before an exact 250-document-page census. No company may be
pruned unless every authoritative ID is present; deletion is limited to both
50 documents and 1% of the remote set, followed by an exact second census.
Other collection import failures remain logged as in the existing producer;
authority/count-read failures and company convergence failures stop the stage.

Tests compare complete producer documents with the retained Python oracle,
exercise prune failures/budgets and pagination invariants, and run the Go
orchestrator against real PostgreSQL plus an HTTP Typesense fixture. That
fixture intentionally has no posting table, proving the normal count path
uses Typesense. Actual production output/resources remain to measure after
release through the supported crawler deployment procedure.

The pre-transaction name snapshot now runs with `--snapshot-taxonomy-names`.
Python passes it over stdin to `--sync-taxonomies --rename-input`; the JSON
handoff is bounded to 8 MiB and accepts only the three name-map kinds. Go
re-reads current names under the shared exporter cursor fence and streams
affected posting IDs in 1,000-row UUID keyset batches. Technology arrays retain
order and duplicates while omitting unknown/null/empty names. Posting updates
remain partial `update` operations: no CDC cursor or ownership is changed.
Existing per-document rejection semantics remain best-effort; ambiguous import
acknowledgements stop the rename stage and are reported without posting values.
The ordinary full sync then publishes the taxonomies and companies. A real
PostgreSQL fixture covers 1,001 affected postings, a retained unaffected row,
null technology IDs, fence ownership, and ambiguous-acknowledgement release.
