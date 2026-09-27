# Stored-copy mining restrictions

27 September 2026. Implementation follow-up to #10090; deployment pending.

## Behavior

An observed `TDMReservedError` from a board monitor records the resource URL,
signal type, optional policy URL and observation time on that board. A detail
scrape records it on the individual posting, using the existing Lightpanda
write-authority fence. These restrictions concern mining eligibility; they
do not delist an employer, remove descriptions or change ordinary search.

Migration 0034 adds `tdm_reserved` and `tdm_reservation` to crawler boards and
postings. A board reservation marks retained postings and advances their CDC
timestamps. A database trigger makes new or reassigned postings inherit a
reserved board's restriction, including concurrent inserts. A detail-only
reservation leaves sibling postings eligible. The state has no automatic expiry;
a missing later header is not a grant of permission.

The board and scraper processors check current database state before choosing
a Python, Go or browser runtime. Detected detail reservations do not enter the
failure/delist ramp or schedule the next fallback extraction step. Existing
search content remains available according to the owner's display decision.

Both Python and Go Typesense export, backfill and reconciliation preserve the
flag. Missing flags on legacy documents are equivalent to false; this avoids
rewriting the entire index solely to add false values.

Narrowed excludes reserved postings from candidate counts, candidate pages and
hydration of saved decisions. It rechecks the index without a cache before
reusing decisions and immediately before each provider call. An unavailable or
malformed eligibility response pauses execution. A reservation learned after
the description was loaded therefore prevents subsequent provider submission
once the reservation reaches the index. Previously returned results are not
erased from a user's already open page.

The labeller excludes reserved postings during sampling and description loading,
rechecks current source rows before rendering a task from local input files,
and rechecks every row before a labelled-dataset upload. Unknown/deleted source
rows or database unavailability refuse those operations. Offline dry-run upload
previews remain available. The shared labeller process lock also covers the two
new database-bearing commands. Legacy batch enrichment rechecks reservations
before submission; experience/salary reprocessing excludes reserved rows.

## Deployment and verification

The earlier #10095 fixes were subsequently included in the successful #10096
rollout (run 36332710204, revision `1f37e47ef036c1b08a5ca45dfca94cd9d7e3dbf6`).
The current deployment checkpoint records c1 reactivated at epoch 80 with 24
selectors; this follow-up still needs its own coordinated release.

The database migration and Typesense schema must be installed **before** the web
version that queries `tdm_reserved`. Keep this change in draft until the crawler
rollout owner has coordinated that order. The active Go/Lightpanda pilot receipt
must be handled through the supported release/rollback procedure; do not remove
it manually or bypass the deploy gate. This task has not changed that receipt.

1. Apply migration 0034 on the crawler database and install the optional boolean
   index field through the existing Go schema setup command. These are additive.
2. Deploy the updated crawler processors and Go Typesense exporter under the
   current runtime ownership procedure. Preserve reservations on application
   rollback; do not downgrade away the evidence columns.
3. Verify the indexed flag on a controlled reserved fixture, then deploy the web
   consumer. A missing field intentionally fails mining queries closed.
4. Rehearse a reservation after a fixture description has been stored: confirm
   source processing skips, the flag reaches Typesense, Narrowed makes no
   provider call, task rendering/upload refuses the copy, and basic search
   still shows the fixture. Use synthetic fixtures, not a production employer.

A synthetic Ashby HTTP response is exercised through the real adapter and board
processor against PostgreSQL: a retained description stops loading for the
labeller and a subsequent stale-config run performs no fetch.

Database integration tests also cover existing/future postings, concurrent insertion,
board versus detail scope and continued listing visibility. Unit tests cover
scrape persistence/skips, Python/Go projections, corpus upload refusal, unavailable
eligibility and a reservation arriving between Narrowed loading and submission.

## Explicit limits and remaining #10090 work

- This is enforcement of **observed** reservations, not a completed discovery
  audit. Origin `/.well-known/tdmrep.json` support and complete active Python,
  Go and browser fetch-path coverage remain in the fetch-path inventory.
- Web consumers see reservations through the normal Postgres-to-Typesense CDC
  path. There is a propagation interval, and an exporter outage prolongs it.
  The web check is against the latest available replica, not a synchronous read
  of the crawler database. Monitor exporter health during the release rehearsal.
- A request already sent to a provider cannot be recalled by a subsequent flag.
  Full removal across databases, R2, caches and historical datasets remains the
  owner's separately deferred first-request procedure.
- Existing historical HF trace bundles have different provenance from labelled
  posting rows. This change does not claim a complete trace-corpus restriction
  lookup, purge or rights clearance. Both Job Seek datasets remain private.
- Releasing a reservation requires an explicit reviewed decision about the
  resource and retained copies. There is no automatic opt-in or clearing CLI in
  this change. Review board and individual evidence before any targeted reset;
  resetting a board alone intentionally leaves its retained postings restricted.

The owner's decisions to keep descriptions as displayed and to avoid a
per-source permission register remain unchanged. These engineering follow-ups
are not presented as an express Paddle application-submission requirement.
