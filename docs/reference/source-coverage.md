---
summary: Private weekly source-coverage export and reviewed public career-page links for Justup; use for the monitor ledger, source-link registry, company allowlist, and snapshot contract.
---

# Source coverage and weekly export

The crawler records each Go monitor's terminal check in the same fenced PostgreSQL transaction as its ordinary-queue result. A check is complete only when the full inventory was processed without truncation, filtering, duplicate identities, or an unverified disappearance. Failed, partial, and gone results cannot prove continuous coverage. A change to a board's company, URL, monitor configuration, interval, browser requirement, enabled state, or approved public career-page link starts a new scope revision. At cycle start, the Go writer also binds the effective monitor configuration and resets the revision when it changes without a registry fingerprint change. The migration starts tracking at installation; it does not reconstruct older coverage from current board or posting state.

The PostgreSQL posting receipt records database insertion time and original board identity independently of the mutable `first_seen_at` field and later active state. The weekly export rejects selected company observations whose board identity is missing or reassigned. A durable loss receipt prevents a deleted or reassigned board from disappearing silently from a selected company's comparison window. The export reads a repeatable, read-only database snapshot and does not expose posting text, monitor configuration, or credentials.

## Review public career-page links

`apps/crawler/data/source_coverage_links.csv` is the reviewed list of links that may appear beside coverage checks. Each row identifies a company and board slug, a public HTTPS career-page URL, and its provenance: `employer-careers`, `ats-careers`, or `reviewed-redirect`. A missing row means no public link is approved; never substitute the crawler's `board_url`, a posting URL, or the company homepage. Review the destination and its redirect behavior before adding or changing a row. The sync rejects credentials, queries, fragments, encoded paths, private or IP hosts, unsafe ports, and duplicate or unknown boards. It stores approved links separately from crawler configuration and does not send them to Redis.

Link changes and removals reset that board's coverage revision. Its history remains recorded, but a 14-day continuous interval is unknown until the new link has its own complete window of checks. Registry sync keeps the approval timestamp when a row is unchanged and updates it only when the reviewed row changes. A board reassignment that no longer matches the reviewed company fails sync; correct or remove its link in the same reviewed data change.

## Produce a weekly artifact

Use a private directory accessible only to the operator and Justup's source reader. Supply an operator-reviewed UTF-8 file with one approved company slug per line; blank lines and lines beginning with `#` are ignored. The exporter includes every current board of every selected company, including disabled and incomplete boards. Public company homepage links retain meaningful paths and canonicalize a bare origin with a trailing `/`; credentials, private hosts, unsafe ports, queries and fragments are rejected. If any selected company has an unsafe or non-HTTPS catalogue homepage, the entire export stops until its official HTTPS metadata is reviewed and corrected. The exporter also rejects unknown companies, provenance gaps, and size limits rather than silently dropping records.

Run the `typesense-exporter` binary with:

```text
--export-source-coverage YYYY-MM-DD approved-company-slugs.txt private-output-directory
```

`YYYY-MM-DD` is the Monday UTC period end. Export only in the following 24 hours. The result is `<private-output-directory>/YYYY-MM-DD.json`, created with mode 0600. The directory must be private. The file contains `{artifactVersion:1,snapshotBytesBase64,sha256}`; the digest covers the exact decoded JSON bytes. A repeat invocation reuses a valid existing artifact. A different artifact for the same period cannot replace it.

The inner snapshot uses `schemaVersion: 2`, `sourceVersion: "jobseek-coverage-v2"`, and a 14-day comparison window. Each selected board has `publicLink: null` or an approved `{url,status,provenance,approvedAt}` object bound to its scope revision. The exporter validates the stored link again and stops on unsafe or mismatched provenance. Each board also gets an explicit complete, interrupted, or unknown interval. Complete requires the same scope revision before the window, a complete check before the starting boundary, a schedule no slower than once per 24 hours, all scheduled checks inside the window within 15 minutes of their due times, and a complete check after the ending boundary. Missing or partial evidence suppresses that board's activity in Justup. Monitor completion incrementally prunes private check and original-observation receipts older than 45 days. Published weekly artifacts and Justup's stored editions provide longer-lived evidence. Existing version-1 period files stay immutable and valid; a version-2 export never replaces them.

The current source reader accepts up to 500 companies, 2,000 boards, 100,000 observations, 4 MiB of decoded JSON, and 6 MiB of wrapper JSON. An oversized export fails and needs a reviewed capacity change. No production export should be copied to a public repository or served by an HTTP route.

## Schema and rollback

Migration `0042` adds `source_coverage_scope`, `source_coverage_check`, `source_coverage_scope_loss`, and `source_coverage_posting_first` with board and posting triggers. Migration `0043` adds the approved public-link table and its scope-revision trigger. The native local migrator and Alembic use the same generated schema. A prior crawler image can run against the additive schema during image rollback. A schema downgrade removes the coverage evidence and must occur only under the production deployment contract after the new writer is stopped; subsequent weekly periods then begin with unknown coverage.
