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
the conservative fallback. Only a company with an actually matched active-job
bucket is impact-known. An inventory-side key alone cannot prove zero because a
job-detail URL may omit the tenant and its display name may drift. Unmatched
companies and families without a job artifact stay `impact_unknown` and rank
after known-active companies, so gaps remain discoverable instead of
disappearing.

Active job count is the primary rank. Unique location count, country coverage,
company name, and a deterministic source URL key break ties. Per-bucket
locations are retained as bounded stable hashes and unioned after fallback
matching, avoiding duplicate counts without storing the source strings. The
compact snapshot also retains remote-job count, country codes, and the latest
published `posted_at` value. Schema drift, corrupt Parquet, partial downloads,
row-count mismatch, configured cache pressure, or the free-space reserve leave
the prior impact pointer untouched.

The preferred upstream improvement is a small `company_stats.parquet`, keyed by
the same company inventory identity and containing active counts/signals. The
next-best improvement is a direct `tenant_key` column on every job row and the
matching key in `companies.csv`. Jobseek will consume either as data, while
continuing to own and run all production monitors.

## Candidate identity and conservative deduplication

Every candidate receives a stable source key beginning
`ats-scrapers:<family>:`. The final component is a locally derived provider
tenant/board identity; it is not copied from or coupled to upstream scraper
code. Provider-host/API/embed aliases and explicit native monitor configuration
are normalized where the native monitor uses one tenant token, while real
board scopes such as Taleo CWS portals, Moka campus
portals, Keka sub-boards, and distinct Workday sites remain distinct. The issue
body contains the readable source key and normalized board URL. Its machine
marker stores a reversible base32 source identity plus a full SHA-256 URL
identity, avoiding HTML-marker injection and lossy URL truncation.

Only these exact facts are hard skips:

- the stable source key already exists in the ledger or a marked GitHub work
  item;
- the normalized board URL already exists in `boards.csv`, the ledger, or a
  marked GitHub work item;
- the exact native ATS plus tenant/board scope is already configured;
- a prior create remains in the ledger even if its remote item has drifted.

Names, slugs, shared homepage domains, parent/subsidiary/region relationships,
and similar company-request or active-PR titles are always soft warnings. They
are attached to the generated issue for the configuration agent and never
discard a candidate. This deliberately preserves acquisitions, renamed
companies, subsidiaries, regional portals, and valid second ATS/board setups.

Local companies and boards are loaded once from their CSV registries. GitHub
company-request issues (open and closed) and active PRs are fetched in paginated
bulk lists; there is no per-candidate GitHub search. Marked issues are
reconciled into `candidates/ledger.sqlite` at startup. Active PR markers remain
ephemeral hard stops and never become durable ledger records. Each successful
create is committed immediately with SQLite WAL enabled. If a create response
is lost, returns an ambiguous 5xx, or has a malformed success body, the
coordinator refreshes the bulk marker index before returning an unknown
outcome. If the process dies between GitHub commit and the local commit, the
next startup repairs the ledger from the marker. A missing remote record is
reported as `remote_missing` and remains a fail-closed hard skip instead of
silently recreating the issue.

Every coordinator-created issue is recorded in a separate `creation_events`
ledger table keyed by GitHub issue number. This makes the UTC-day creation cap
survive restarts while keeping startup reconciliation of older remote markers
from consuming today's budget. If GitHub commits just before a process crash,
startup uses the trusted import label and GitHub `created_at` timestamp to
reconstruct the missing event; unlabelled contributor markers cannot consume
the daily budget.

## Bounded resolver queue

The queue runner counts every open `company-request`, including human requests
and imports. An issue is resolver-available unless it has a `ws` claim newer
than four hours or an open PR body that closes/fixes/resolves the issue. Recent
repository comments, issues, and PRs are fetched in bulk; candidate selection
never performs per-company or per-issue searches. Invalid claim timestamps are
fail-closed and visible in the report.

Refill starts only below 450 available issues, targets 500 available issues,
and never plans beyond 600 total open issues. Creation is additionally bounded
to 25 per invocation and 50 per UTC day. `--queue-rollout-cap` is restricted to
the deliberate canary stages 1, 5, and 25 and defaults to 1. The smallest of
the target deficit, hard-cap capacity, tick budget, daily budget, and rollout
stage wins.

Before every production POST, the runner bulk-refreshes the live open-request
count and refuses to fill the final hard-cap slot, reserving it for one
concurrent external writer. GitHub does not provide an atomic conditional
issue-create API, so multiple human/API creates in the tiny interval between
that read and POST cannot be serialized by this runner; the live recheck and
reserved slot are the conservative boundary available without a separate
admission service.

Candidates are sorted with the same stable impact key used by the impact
snapshot. The runner scans past exact hard duplicates until it has the needed
number of eligible companies, so an already-configured high-ranked row does
not waste a queue slot. Soft duplicates remain in the issue body for `ws`.
Created issues carry both `company-request` and `source:ats-inventory`; the
source label must be provisioned before the first canary.

### `ws` native-monitor fast path

The issue also carries a content-addressed candidate marker plus redundant
readable fields for source key, upstream family, native ATS identity, exact
tenant, and normalized board URL. `ws new --issue N` fetches that evidence and
preselects a native Jobseek monitor only when GitHub also attached the protected
`source:ats-inventory` label and every field, the URL hash, tenant identity, and
`ats_inventory.compat` mapping agree. A self-consistent marker in an unlabeled
human issue is not trusted. Immediately before seeding, `ws` rechecks the
current CSV registry for exact normalized URL and native ATS tenant matches;
new evidence disables the fast path. The queue never supplies or executes
upstream scraper code.

The preselected config is named `inventory-seed`. `ws run monitor ... --config
inventory-seed` must succeed with one or more live jobs before the fast path is
marked verified. A stale URL, monitor error, zero jobs, wrong tenant, unknown
family, or evidence mismatch leaves the config unready and sends the agent
through ordinary probe/discovery. Human-created issues without the marker use
the original workflow unchanged. Rerendering `ws task` does not retest a seed
already marked `verified` or `fallback`.

Verification does not reduce the rest of `ws`: duplicate/company research,
metadata, logos, global and regional board discovery, live count comparison,
feedback, overlap checks, CSV validation, and PR gates all remain mandatory.
The source marker is preserved in the resulting PR body so later runner ticks
can reconcile the candidate while work is active.

Creates are sequential, paced by the GitHub client, and separated by bounded
jitter. Primary remaining/reset headers and `Retry-After` are retained in the
report. A 429, primary-limit 403, or recognized secondary-limit 403 stops the
refill cleanly and records the later of the primary reset and retry delay; it
does not loop or burst. Permanent permission/policy 403s remain actionable
errors instead of being retried as throttling. Rate limits during support,
work-item, or claim preflight also produce a structured `rate_limited_preflight`
report. The cache-wide
non-blocking lock rejects a concurrent invocation. Ambiguous create outcomes
still use the marker reconciliation path described above.

Production `refill` automatically reconciles unsupported families through the
one-per-family support-issue path first. If classified candidate coverage falls
below the safety gate, the queue report becomes `coverage_quarantined` with
zero admissions; supported company issues do not leak through while monitor
support is incomplete.

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

# Explain exact hard skips and all advisory matches for the top ranked rows.
# This mode never creates company issues.
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --impact \
  --candidate-issues plan \
  --candidate-limit 100

# Reconcile unsupported families without writes
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --support-issues plan

# Production family-level issue creation (explicit write mode)
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --support-issues create

# Read-only resolver queue health (impact cache is not required)
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --candidate-issues report

# Simulate the exact stage-1 refill, including candidate selection
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --impact \
  --candidate-issues dry-run \
  --queue-rollout-cap 1

# Stage-1 production canary. Unsupported families are automatically
# reconciled into one support issue each before candidate admission.
GH_TOKEN=... uv run crawler ats-inventory \
  --cache-dir /var/lib/jobseek/ats-inventory \
  --impact \
  --candidate-issues refill \
  --queue-rollout-cap 1
```

The normal command emits a structured `ats_inventory.complete` log record. An
interactive terminal also receives a readable JSON report.

## Hetzner deployment and rollout

Production runs as `jobseek-ats-inventory.timer` on the ordinary crawler host,
not as a Codex task. The persistent daily timer uses a 45-minute randomized
delay. Its hardened one-shot resolves the immutable crawler image already
deployed in `/home/deploy/.env`, mounts only the persistent cache subdirectory
and one short-lived GitHub App installation-token file, and invokes the
installed `crawler` entry point directly. It never installs or executes
upstream code.

The GitHub App private key is delivered with systemd `LoadCredential`. A
host-side helper signs a nine-minute JWT with OpenSSL, mints an installation
token, and writes that token to a mode-`0600` temporary file. The container sees
the file through a read-only bind mount; the token value is absent from Docker
arguments, environment metadata, reports, and logs. The token file and bounded
run log are deleted on every exit path. The wrapper first refreshes source and
impact data without GitHub credentials, then mints the installation token and
runs the cached GitHub queue pass inside a 45-minute budget. Long artifact
downloads therefore cannot age out the one-hour installation token.

The persistent root is `/var/lib/jobseek-ats-inventory`. Source and impact
caches remain bounded by their existing 256/768 MiB limits, raw Parquet files
remain transient, status history retains 32 runs, and the ledger survives
disable/rollback. The wrapper has a non-blocking host lock in addition to the
cache lock, a four-hour service cap, 1.5 GiB memory limit, one CPU, PID cap,
read-only container root, dropped capabilities, and no-new-privileges. A
streaming logger mirrors output to journald while retaining at most a 16 MiB
parseable tail for status extraction.

Every run records a credential-free operator status at
`/var/lib/jobseek-ats-inventory/status/current.json`: inventory freshness and
coverage, impact counts, human/import queue counts, issue creates, GitHub rate
state, imported issue claim/PR/terminal counts, sampled pickup latency, refill
events, and the last successful report when a later attempt fails. The crawler
host sampler publishes the bounded `jobseek_ats_inventory_*` aggregates; full
reports remain on the host and structured summaries remain in journald.

### Operator controls and rollback

The write gate is fail-closed on first install. Configuration and enablement
are deliberately separate:

```bash
# Inspect state; never prints credentials.
/usr/local/sbin/jobseek-ats-inventory-control status
systemctl status jobseek-ats-inventory.timer jobseek-ats-inventory.service --no-pager
journalctl -u jobseek-ats-inventory.service -n 200 --no-pager

# Report-only source/cache/queue run. The disabled sentinel forces report mode.
systemctl start jobseek-ats-inventory.service

# Render the exact stage-1 candidate without writes.
/usr/local/sbin/jobseek-ats-inventory-control configure dry-run 1
/usr/local/sbin/jobseek-ats-inventory-control enable
systemctl start jobseek-ats-inventory.service

# Admit exactly one candidate only after reviewing the dry run.
/usr/local/sbin/jobseek-ats-inventory-control configure refill 1
systemctl start jobseek-ats-inventory.service

# Emergency/rollback gate: takes effect before the next container starts and
# retains every cache, report, and ledger file.
/usr/local/sbin/jobseek-ats-inventory-control disable
```

`disable` writes the gate first and then stops any active service. The wrapper
also rechecks the gate immediately before its GitHub phase. Deployment waits
for the exact reviewed crawler image, snapshots the prior host surface, stops
the timer and active service, installs transactionally, and restores the prior
surface and scheduling state on failure.

After stage 1, record the created issue/source key, resolver claim time,
verified/fallback/PR/closed outcome, and the exactly-one replacement refill in
#6190. Repeat those gates at cap 5. Before moving to cap 25, test `disable`, run
the service once, and prove the effective mode was `report` with zero creates;
then re-enable only after configuring `refill 25`. The daily cap remains 50,
the per-tick cap remains 25, all open requests remain below 600, and bootstrap
toward 500 occurs over multiple daily/manual evidence-gated runs.
