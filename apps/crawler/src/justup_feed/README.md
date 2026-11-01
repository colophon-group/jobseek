---
summary: Run and verify the read-only private posting feed for Justup.
read_when: Preparing a Justup import, reviewing posting freshness, or retiring Jobseek search and assets.
---

# Transfer public postings to Justup

This command reads the crawler's local PostgreSQL database and writes a private,
versioned file bundle for Justup. It does not write to the crawler database,
contact Justup, or use Typesense or the old public job asset host. Run it with a
dedicated PostgreSQL role granted only the listed tables and views. Keep its
`LOCAL_DATABASE_URL` in a protected environment, outside Git and logs.
The role also needs enough read visibility into `pg_locks` and
`pg_stat_activity` for the existing writer-barrier query to identify active
posting writers; a role that cannot prove the cutoff fails closed. Do not grant
write privileges merely to make the exporter run.

The feed is an operator-run transfer. A schedule, host deployment, data-copy path,
and accepted Justup importer build must be qualified separately before product
freshness can be claimed. A successful local export alone does not activate a
fresh feed.

## Export and acknowledge

Create a local directory outside the checkout, owned by the operator and mode
`0700`. The command creates all files in it with mode `0600`. An optional exact
company selector has the same private schema as Justup's importer:

```json
{"schemaVersion":1,"companyIds":["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}
```

Use a real, reviewed selector file; the shown UUID is synthetic. At most 100
canonical unique company UUIDs are accepted. Without a selector, export covers
all source postings in the 14-day window and fails if the safety caps are
exceeded. A selected export never falls back to global coverage.

```sh
uv run --frozen python -m src.justup_feed export \
  --directory /private/justup-feed \
  --company-ids-file /private/justup-company-ids.json
```

The first export writes a full source snapshot. Later exports write changed
posting rows in batches of at most 100. Run `--reconcile` to write a full
snapshot again, including after changing the company selector. Run a verified
full snapshot at least once inside Justup's 24-hour evidence lifetime; changes
alone cannot reverify postings that have not changed. Export the next batch only
after Justup has imported and verified the current manifest and every part.

The command returns the pending manifest path, such as
`batch-<id>/manifest.json`, and SHA-256. Parts are relative to that batch
directory. The exporter writes a complete batch in a private staging
directory, atomically renames it, then records the pending batch in
`state.json`. An unpublished staging or batch directory with no state
reference, or an interrupted private state-write temp file, is removed on the
next locked run. Re-running with an
unacknowledged batch returns that same manifest. For a partial `changes`
batch (`complete:false`), Justup may acknowledge only after it has durably
staged the exact verified batch and can recover or replay it without Jobseek.
That acknowledgement is a staging receipt, not a claim that search results
have been refreshed. For a final `complete:true` batch or full snapshot,
Justup writes a receipt only after a successful atomic application import.
Every receipt contains exactly:

```json
{"schemaVersion":1,"manifestSha256":"<lowercase SHA-256 of the exact manifest bytes>"}
```

Then acknowledge that receipt:

```sh
uv run --frozen python -m src.justup_feed ack \
  --directory /private/justup-feed \
  --receipt /private/justup-import-receipt.json
```

Acknowledgement advances this feed's local cursor to the manifest's
`toCursor`. A missing or mismatched receipt leaves the batch pending. If the
final application import fails, keep the final batch unacknowledged and replay
it; previously acknowledged partial pages must remain durable in Justup staging.
The operator must retain the manifest and parts through Justup acceptance and
backup/restore verification. A crash before acknowledgement is replayed as the
same batch; Justup must accept an exact retry idempotently.

Acknowledged bundles remain in the private directory. A verified backup job
may produce a separate receipt containing the accepted manifest digest and
`backupVerified:true`:

```json
{"schemaVersion":1,"manifestSha256":"<accepted manifest SHA-256>","backupVerified":true}
```

Record it with:

```sh
uv run --frozen python -m src.justup_feed backup-verified \
  --directory /private/justup-feed \
  --receipt /private/justup-backup-receipt.json
```

The exporter prunes an acknowledged bundle only after both this explicit
backup receipt and seven full days since Justup acceptance. Pending bundles
are never pruned. The total spool is capped at 8 GiB and 4,096 accepted
bundles. If backup verification or cleanup lags, new exports fail closed
instead of consuming unbounded disk. The backup receipt must come from the
actual restore-verified backup process; this command cannot verify a remote
backup merely from the JSON assertion.

## Bundle contract

The private manifest has `schemaVersion:1`, `kind` (`snapshot` or `changes`),
`fromCursor`, `toCursor`, `cutoffAt`, `complete`, `scope`, `windowDays:14`,
`sourceScopeRowCount`, `rows`, and ordered `parts` entries with filename,
SHA-256 and row count. Cursors are `{updatedAt,id}`. `scope` is either
`{kind:"all_eligible"}` or `{kind:"company_ids",requestedCount,
requestedSetSha256}`. The digest is SHA-256 of compact JSON containing the
sorted selected UUIDs; the UUID list is not serialized. The scope is bound to
the cursor. A scope change requires a full reconciliation snapshot before
incremental batches can continue.

Each part is a UTF-8 JSON object with `schemaVersion:1` and `rows`. The row
allowlist is stable posting/company IDs, public company name/slug/website,
title, original
source URL, active/reserved/has-content states, original first-seen and latest
observation/update times, locales, aligned direct location IDs, names, workplace
and geographic types, employment, salary, experience, occupation and seniority
facts, plus `body` and `bodyStatus`. Unknown facts are null. No board IDs,
monitor or scraper configuration, credentials, internal evidence, or user data
are selected. `body` is null or `{locale,html,sha256}`. Its HTML is private
server-side evidence; Justup normalizes it and never sends it as a user answer.

The producer selects an uploaded per-locale description row from `descriptions`,
recomputes the signed 64-bit hash from the first eight SHA-256 bytes over its
UTF-8 HTML, and compares that with the row's `hash`. On success it includes the
full SHA-256 for transfer verification. A missing, oversized, or mismatched body
is null with an explicit `bodyStatus`; the structured posting remains available.
`hasContent` reflects the crawler's title and content pointer, independently of
whether this transfer could supply body evidence. The old public mutable
`latest.html` URL is not part of this contract.

An initial or reconciliation snapshot covers all currently active source rows
first seen within 14 days of `cutoffAt`, within the exact selected company scope
if supplied, and with `updated_at < cutoffAt`. `sourceScopeRowCount` counts
those raw source rows, including reserved and body-unavailable records. It does
not assert Justup has imported every row; Justup currently selects at most
1,000. The exporter refuses more than 100,000 snapshot rows, 512 MiB total
parts, 64 MiB per part, or 512 KiB per body, and leaves the prior cursor intact.
Failure is a limited-coverage/unavailable state, never proof that the employer
has no open jobs.

The snapshot is read under a repeatable-read, read-only transaction. The
existing posting writer-barrier cutoff is captured **before** that transaction
establishes its user-table snapshot. A transaction already writing at cutoff
capture is excluded until a later batch; a transaction that commits between
cutoff capture and the snapshot is either visible or has an update timestamp at
or after the cutoff. Changed rows use the `(updated_at,id)` keyset and strict
`updated_at < cutoffAt` bound. The cursor advances only after Justup accepts
the complete bundle. `changes` rows include inactive and reserved transitions.
Absence from a snapshot only means absent from that exact active/window/scope
slice; it is never an observed closure.

Each database query has a 60-second timeout and the export has a 10-minute
overall duration limit. A timeout leaves the previous cursor unchanged; any
incomplete staging is removed on the next locked run. These ceilings are
operational limits, not evidence that a source with more rows is empty.

## Remaining source boundaries

Hard deletes have no tombstone in the current posting table. Company name and
taxonomy edits can occur without stamping a posting. Full reconciliation
snapshots refresh those facts and remove absent postings from visible coverage,
but must not fabricate employer closure times. A durable historical observation
feed for Hiring Wire and source-scope coverage remains a separate producer
contract. Keep the old search/assets readers available until the exact Justup
consumer build, snapshot, changes, closures, body hashes, replay, selected
scope, and natural refresh cadence have been verified end to end.
