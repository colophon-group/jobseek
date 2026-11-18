---
summary: Private, read-only weekly source-coverage export for Justup Hiring Wire; use when operating or changing the Go monitor ledger, company allowlist, or snapshot contract.
---

# Source coverage and weekly export

The crawler records each Go monitor's terminal check in the same fenced PostgreSQL transaction as its ordinary-queue result. A check is complete only when the full inventory was processed without truncation, filtering, duplicate identities, or an unverified disappearance. Failed, partial, and gone results cannot prove continuous coverage. A change to a board's company, URL, monitor configuration, interval, browser requirement, or enabled state starts a new scope revision. At cycle start, the Go writer also binds the effective monitor configuration and resets the revision when it changes without a registry fingerprint change. The migration starts tracking at installation; it does not reconstruct older coverage from current board or posting state.

The PostgreSQL posting receipt records database insertion time and original board identity independently of the mutable `first_seen_at` field and later active state. The weekly export rejects selected company observations whose board identity is missing or reassigned. A durable loss receipt prevents a deleted or reassigned board from disappearing silently from a selected company's comparison window. The export reads a repeatable, read-only database snapshot and does not expose posting text, monitor configuration, or credentials.

## Produce a weekly artifact

Use a private directory accessible only to the operator and Justup's source reader. Supply an operator-reviewed UTF-8 file with one approved company slug per line; blank lines and lines beginning with `#` are ignored. The exporter includes every current board of every selected company, including disabled and incomplete boards. Public company homepage links retain meaningful paths and canonicalize a bare origin with a trailing `/`; credentials, private hosts, unsafe ports, queries and fragments are rejected. If any selected company has an unsafe or non-HTTPS catalogue homepage, the entire export stops until its official HTTPS metadata is reviewed and corrected. The exporter also rejects unknown companies, provenance gaps, and size limits rather than silently dropping records.

Run the `typesense-exporter` binary with:

```text
--export-source-coverage YYYY-MM-DD approved-company-slugs.txt private-output-directory
```

`YYYY-MM-DD` is the Monday UTC period end. Export only in the following 24 hours. The result is `<private-output-directory>/YYYY-MM-DD.json`, created with mode 0600. The directory must be private. The file contains `{artifactVersion:1,snapshotBytesBase64,sha256}`; the digest covers the exact decoded JSON bytes. A repeat invocation reuses a valid existing artifact. A different artifact for the same period cannot replace it.

The inner snapshot uses `sourceVersion: "jobseek-coverage-v1"` and a 14-day comparison window. Each selected board gets an explicit complete, interrupted, or unknown interval. Complete requires the same scope revision before the window, a complete check before the starting boundary, a schedule no slower than once per 24 hours, all scheduled checks inside the window within 15 minutes of their due times, and a complete check after the ending boundary. Missing or partial evidence suppresses that board's activity in Justup. Monitor completion incrementally prunes private check and original-observation receipts older than 45 days. Published weekly artifacts and Justup's stored editions provide longer-lived evidence.

The current source reader accepts up to 500 companies, 2,000 boards, 100,000 observations, 4 MiB of decoded JSON, and 6 MiB of wrapper JSON. An oversized export fails and needs a reviewed capacity change. No production export should be copied to a public repository or served by an HTTP route.

## Export evidence of current monitoring

The weekly artifact describes a historical interval. For the status of an
approved board now, the offline monitoring receipt reads the latest terminal
check of its exact scope revision in a read-only, repeatable PostgreSQL
transaction. The producer checks that the board still belongs to the company
and is active with the current configuration bound.

Run the receipt command from the repository root in the protected source environment
with `LOCAL_DATABASE_URL` supplied by the existing operator credential boundary.
Give it an absolute path to a private mode-0600, single-link JSON approval file
owned by the operator running the command. The approval has exactly
`schemaVersion:1`, `companyId`, `boardId`, and `revision`. The three identifiers
are canonical UUIDs for the separately reviewed Jobseek scope.

```sh
uv run --project apps/crawler --frozen python scripts/justup_monitoring_receipt.py /private/approved-monitoring-scope.json
```

The command writes a single `jobseek-monitoring-v1` receipt to standard output
with the approved identifiers, state, reason and observation time. A current
receipt also carries the complete check's verification time and expiry. Expiry
is capped by the scheduled next check and configured cadence with 15 minutes
of grace and by 24 hours after verification. A moved or changed scope, a disabled
board, a missing or incomplete latest check, an invalid schedule, or a missed
check produces an unavailable receipt. The command reads no listings,
configuration contents, requests or account data and changes no crawler state.

Retain the receipt privately and transmit its exact byte digest through the
existing protected operator approval path. A consumer must bind the bytes to
that independent digest and approved identity, reject stale or superseded
positive receipts and let newer unavailable receipts withdraw their claims.
The receipt alone does not enable an application or notification schedule.

## Schema and rollback

Migration `0042` adds `source_coverage_scope`, `source_coverage_check`, `source_coverage_scope_loss`, and `source_coverage_posting_first` with board and posting triggers. The native local migrator and Alembic use the same generated schema. A prior crawler image can run against the additive schema during image rollback. A schema downgrade removes the coverage evidence and must occur only under the production deployment contract after the new writer is stopped; subsequent weekly periods then begin with unknown coverage.
