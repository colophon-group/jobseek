# ATS inventory candidate runner

Jobseek uses the public `kalil0321/ats-scrapers` artifact inventory only as a
source of company candidates and impact data. Upstream Python/TypeScript scraper
code is never imported, installed, or executed. Every production crawl uses a
Jobseek-owned monitor and scraper.

## Source boundary

`crawler ats-inventory` fetches
`https://storage.stapply.ai/jobhive/v1/manifest.json` and the manifest's
aggregate `companies.csv` artifact (`ats,name,slug,url`). Artifact URLs and
redirect destinations must remain HTTPS URLs below
`storage.stapply.ai/jobhive/v1/`.

The cache contains:

- `objects/<sha256>`: immutable manifest and company-inventory bodies;
- `snapshots/<manifest-sha256>.json`: validated snapshot metadata;
- `current.json`: the atomically replaced last-known-good pointer.

With `--impact`, the `impact/` subdirectory additionally contains:

- `families/<ats>/<parquet-sha256>.json`: compact tenant buckets derived from
  one validated per-ATS jobs artifact;
- `snapshots/<sha256>.json`: complete per-company counts and secondary signals;
- `current.json`: the atomically replaced impact-snapshot pointer.

The command sends `If-None-Match` on later runs. An unchanged manifest does not
download the company inventory again. A changed manifest may reuse the existing
company object when its SHA-256 is unchanged.

The CLI holds a cache-wide non-blocking `runner.lock` across source refresh and
GitHub reconciliation, and source refresh has its own single-writer lock for
library callers. This prevents overlapping timers from racing cache publication
or marker-list/create issue reconciliation on the single Hetzner runner host.

Before publishing `current.json`, ingestion verifies manifest version and
timestamps, trusted artifact URLs, checksums and byte sizes, the exact CSV
header, row/family counts, non-empty fields, safe HTTPS company URLs, normalized
URL uniqueness, and bounded total/per-family shrinkage. Invalid updates leave
the last-known-good pointer and objects untouched. Recent snapshots have count,
age, and total-size bounds.

Publication is monotonic by manifest `generated_at`; a delayed older fetch
cannot replace a newer snapshot. If a local object is corrupt while the server
returns 304, ingestion validates before touching `current.json`, retries once
without the ETag, and repairs the cache only through the complete validation
path.

## Impact derivation

Impact refresh reads only the manifest's changed per-ATS Parquet artifacts for
candidate-eligible families. It never requests the aggregate all-jobs CSV or
Parquet. Each changed family is streamed to a bounded temporary file, checked
against the published size and SHA-256, scanned with Polars, reduced to compact
tenant buckets, and then removed. Keeping raw artifacts would make frequent
upstream revisions require multiple copies of a corpus larger than 1 GiB; the
checksum-addressed derived object is the durable cache instead.

The per-family objects are independent of the company inventory. A partially
completed first refresh can therefore resume without redownloading families it
already derived. A company-inventory-only update remaps those cached buckets
without touching Parquet. Normal unchanged runs load the compact current
snapshot and perform zero job-artifact requests.

The current published schema has no universal ATS tenant column. Small local
extractors identify tenants from stable URL/query/path components (and, for
Paylocity, the tenant prefix in `ats_id`); exact unique company-name matching is
the conservative fallback. A resolvable company with no rows is known to have
zero active jobs. Ambiguous companies and families without a job artifact stay
`impact_unknown`; they are ranked after known-active companies but before
confirmed-zero companies, so gaps remain discoverable instead of disappearing.

Active job count is the primary rank. Unique location count, country coverage,
company name, and a deterministic source URL key break ties. The compact
snapshot also retains remote-job count, country codes, and the latest published
`posted_at` value. Schema drift, corrupt Parquet, partial downloads, row-count
mismatch, configured cache pressure, or the free-space reserve leave the prior
impact pointer untouched.

The preferred upstream improvement is a small `company_stats.parquet`, keyed by
the same company inventory identity and containing active counts/signals. The
next-best improvement is a direct `tenant_key` column on every job row and the
matching key in `companies.csv`. Jobseek will consume either as data, while
continuing to own and run all production monitors.

## Compatibility and quarantine

`apps/crawler/src/ats_inventory/compat.py` is the explicit compatibility source
of truth. It maps upstream names to the canonical Jobseek monitor, including
aliases such as `join_com -> join`, `oracle -> oracle_hcm`, `moka -> mokahr`, and
`beisen_legacy -> beisen`. SuccessFactors and Teamtailor use the shared `rss`
monitor presets. Known job marketplaces are explicit exclusions rather than
company candidates.

The report distinguishes:

- supported candidate rows;
- unsupported candidate rows and families;
- explicitly excluded marketplace rows;
- classified coverage and candidate coverage;
- whether candidate generation remains above the 99% safety gate.

An unknown family is never turned into company-request issues. The support-issue
path reconciles all existing open and closed issues by the stable marker
`<!-- ats-inventory-support:family=<family> -->`. It creates at most one issue
per family, includes representative URLs and published row counts, and surfaces
a still-unsupported closed issue instead of creating a duplicate. A transport
failure after issue creation is reconciled by marker before a later retry.

## Operator command

From `apps/crawler/`:

```bash
# Source/cache validation only (default; no GitHub calls)
uv run crawler ats-inventory --cache-dir /var/lib/jobseek/ats-inventory

# Refresh changed per-family job artifacts and publish compact impact
uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --impact

# Reconcile unsupported families without writes
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --support-issues plan

# Production family-level issue creation (explicit write mode)
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --support-issues create
```

The normal command emits a structured `ats_inventory.complete` log record. An
interactive terminal also receives a readable JSON report. The eventual queue
refill service uses this source snapshot, the derived job-impact snapshot, and
the deduplication ledger described by #6186-#6190.
