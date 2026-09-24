# Typesense posting-index pruning — September 23, 2026

## Decision under test

Follow-up to [#9919](https://github.com/colophon-group/jobseek/issues/9919).
Remove four unused display-field indexes on Typesense 27.1. Defer the seven
numeric/boolean sort-index removals because their live rebuild can change
consumed filter/facet results temporarily (reproduction below). Keep the documents, stored display values, all consumed
filter/facet indexes, salary range facets, and the active-only numeric candidate
order used by feed narrowing. Inactive postings remain available for UI counts.

This is separate from the [merged keyword-query optimization](2026-09-23-typesense-query-performance.md).
Those measurements establish a latency improvement under concurrent load, not a
steady RSS reduction. Earlier sample allocator-active reductions of 8.0% for
display indexes and 13.4% for sort indexes are hypotheses for this rehearsal;
they are neither additive nor full-corpus RSS predictions.

## Field/consumer review

The [complete field matrix](../typesense-footprint-investigation-2026-08-26.md#field-consumer-matrix)
was rechecked against the current server/browser search, posting-detail, company,
taxonomy, watchlist, exporter and reconciliation readers. The dispositions for
this change are:

| Fields | Used for | Disposition |
| --- | --- | --- |
| `company_name`, `location_names`, `seniority_name`, `technology_names` | Returned display values; exporter and reconciliation payloads | Keep stored; `index: false`, `optional: true`; remove unused text/facet indexes |
| `is_active`, `has_content` | Active/content filtering and state | Retain all existing index/facet/sort structures in this rollout |
| `seniority_id` | Filtering, facet choices, localized detail lookup | Retain all existing index/facet/sort structures in this rollout |
| `experience_min`, `experience_max` | Legacy overlap filtering; min histogram | Retain all existing index/facet/sort structures in this rollout |
| `experience_min_years`, `experience_max_years` | Decimal overlap filtering and returned experience | Retain all existing index/facet/sort structures in this rollout |
| `first_seen_at` | Freshness order and historical cutoff | Retain index, sort and collection default sort |
| `salary_eur` | Salary filters and numeric range facets | Retain index, facet and sort; 27.1 range facets need the sort structure |
| `candidate_order_hi`, `candidate_order_lo` | Stable feed-narrowing enumeration | Retain numeric sort/index; do not change producer or active-only population |
| `candidate_order_key` | UUID identity verification | Keep stored |
| `id`, `title`, `company_id`, `reconciliation_bucket` | Identity, title search, company grouping/counts, partitioned exports | Unchanged |
| `location_ids`, `location_direct_ids`, `location_types`, `occupation_ids`, `technology_ids`, `employment_type`, `locales` | Filters and consumed facets | Unchanged |
| Remaining stored-only fields | Display, compatibility, source URLs and reconciliation | Unchanged; no repeat claim for previously removed indexes |

No current application query sorts by the seven deferred sort fields or searches/
facets on the four display names. The schemas of the separate company and
taxonomy collections are unchanged. Numeric facet `avg`, `min`, `max` and `sum`
have no application readers; bucket labels/counts and `total_values` do.

## Migration and rollback contract

The canonical schema lives in `apps/crawler/src/typesense_schema.py`.
The existing setup patcher repairs index drift with one drop/add pair per PATCH.
Only the four display fields change. Sort/facet/optional differences by
themselves do not trigger rebuilds; a regression test keeps consumed fields out
of an automatic sort-only migration.
Setup re-reads the schema between patches; rerunning it is idempotent.

Each PATCH can scan the full collection and temporarily block writes. Rebuilding
one field at a time bounds overlapping work but does not prove a safe RSS peak.
No second full posting collection is created on production.

Capture the complete live schema before rollout. Rollback requires explicitly
drop/adding the four original field definitions, one at a time. Reverting the
source change and rerunning setup also restores their index flags. The lab's
`rollback --field NAME` uses its captured baseline definitions. It accepts no
remote host or production credential, so it is a rehearsal tool, not a production
rollback command. A live rollback must be coordinated with the deploy setup
version to avoid immediately reapplying pruning.

## Measurement plan and acceptance limits

The local lab uses a freshly acquired complete posting export, imported into one
owned Docker container with a 6 GiB memory/swap limit, four CPUs, pinned 27.1 image
and loopback-only API. Data and snapshots use dedicated named volumes. The export
was acquired in 256 reconciliation buckets with CDC still running; it is a static
test corpus after acquisition, not a transactionally consistent source snapshot.
Production collection counts were 5,553,766 before and 5,554,590 after acquisition.

The acquired corpus contains **5,554,279 postings** from 5,957 companies:
2,361,415 active and **3,192,864 inactive**. The historical UI filter includes
3,047,792 of those inactive postings. Every active posting has both candidate
order words; no inactive posting does. All four experience scalars are populated
on every posting, whereas seniority is populated on 842,811. The source is
5,565,850,147 bytes uncompressed and 1,220,349,142 bytes compressed.
[Aggregate counts and full-source fingerprints](typesense-index-pruning-2026-09-23/corpus.json).

The local host is ARM64. Native ARM64 results are useful full-cardinality evidence
but do **not** satisfy the issue's x86_64 production-equivalence gate. An x86 image
under QEMU was rejected for memory acceptance because jemalloc reported different
`MADV_DONTNEED` behavior under emulation.

Before accepting a rollout:

1. Require identical stored-document count and order-independent full-document
   fingerprints before migration, after migration, after restart and after rollback.
2. Require identical consumed query results: company/posting counts, group and hit
   order, returned documents, complete facet value/count maps, salary bins,
   decimal-experience filters, inactive/year counts and stable candidate order.
   Any mismatch or cutoff needs investigation; do not silently discard it.
3. Measure actual process RSS from `/proc/1/smaps_rollup`, cgroup charge and
   anon/file/kernel breakdown at one-second intervals, plus allocator allocated
   and active bytes separately. Typesense's `resident_bytes` is not actual RSS.
   Report import, alteration, rebuild, snapshot and query peaks, OOM events and
   memory-limit reclaim events. A sampled peak is a lower bound, not a guarantee.
4. Require a repeatable RSS/anonymous-memory decrease after matched warm-up and
   settling windows. Compare both post-alter and post-restart states; distinguish
   retained allocator pages from live index allocation and file-cache reclaim.
5. Use two warm-ups and fifteen timed uncached searches per case. Treat a p95
   increase exceeding both 10% and 2 ms as a failed initial latency screen;
   investigate with repeated/counterbalanced runs before accepting it. The 2 ms
   floor handles integer-millisecond noise, not slower broad searches.
   Repeat grouped software, sales and remote-English queries in bursts of four
   concurrent requests. Record both server time and local HTTP wall time.
6. Require no OOM kill or unexplained restart, successful snapshot/rebuild,
   delist/relist CDC behavior, and reconciliation parity. Production acceptance
   additionally requires native x86_64 evidence and a post-rollout health/RSS/
   latency/reconciliation soak. The local fixture is not a CDC catch-up soak.

The `cdc` lab command replays a bounded batch of up to 500 active documents,
using the producer's actual inactive candidate-key representation. It requires
exact active/inactive count deltas, restores the original documents even on a
failed assertion, and retrieves every affected payload to check exact restoration.
The baseline and selected full-corpus runs also repeat query parity after this
batch. This tests write semantics and memory peaks; it is not a live PostgreSQL
cursor/catch-up measurement.

Readiness requires three observations of an empty `pending_write_batches` queue,
exact document count after restart, and stable semantic/allocation probes.
`/health` alone returned true while an interrupted import was still replaying.
The restart command now takes a snapshot first, records checkpoint/restart/ready
timestamps separately, and removes only its own temporary checkpoint afterward.
The first baseline restart began before that harness revision and replayed the
import journal; its artifact is retained as recovery evidence. A separate
`baseline-checkpoint` measurement follows a checkpointed rebuild and is the
matched baseline for the selected/rollback rebuild comparisons. Colima free-block
trim ran during that initial journal replay to protect host disk headroom; it
must not be treated as an uncontended rebuild timing result. The host also
suspended during that journal replay; its duration is excluded. An idle-sleep
assertion is held for the remaining rehearsal, and matched latency measurements
start only after recovery completes.

## Reproduction

Use `scripts/typesense-rss-suite.py` for the complete sequence, or
`scripts/typesense-rss-lab.py` for individual phases. Neither accepts production
credentials. The input directory contains `schema.json`, `postings.jsonl.gz` and
`corpus.json` (the complete source count/hash and frozen historical cutoff).
The suite aborts on incomplete imports, document/query drift, live-read failures,
cutoffs, non-idempotent setup or OOM. RSS and latency still require comparison of
the resulting artifacts; a zero exit status alone is not performance acceptance.

```sh
# Run with the crawler environment, or install the locked typesense/httpx/structlog versions.
uv run --directory apps/crawler python ../../scripts/typesense-rss-suite.py \
  --root /tmp/pruning-lab --input /private/pruning-input
```

Individual phases are also available:

```sh
python3 scripts/typesense-rss-lab.py init --root /tmp/pruning-lab --schema /private/schema.json
python3 scripts/typesense-rss-lab.py import --root /tmp/pruning-lab --sample /private/postings.jsonl.gz
python3 scripts/typesense-rss-lab.py restart --root /tmp/pruning-lab --label baseline-rebuild
python3 scripts/typesense-rss-lab.py measure --root /tmp/pruning-lab --label baseline
python3 scripts/typesense-rss-lab.py fingerprint --root /tmp/pruning-lab --label baseline-fingerprint
python3 scripts/typesense-rss-lab.py snapshot --root /tmp/pruning-lab --label baseline-snapshot
python3 scripts/typesense-rss-lab.py measure --root /tmp/pruning-lab --label baseline-concurrent --concurrency 4 --cases keyword_current keyword_sales keyword_remote
uv run --directory apps/crawler python ../../scripts/typesense-rss-lab.py setup --root /tmp/pruning-lab --label selected-migration
python3 scripts/typesense-rss-lab.py measure --root /tmp/pruning-lab --label selected
python3 scripts/typesense-rss-lab.py fingerprint --root /tmp/pruning-lab --label selected-fingerprint
```

Also repeat `setup` to verify idempotence, restart/measure/fingerprint/snapshot the
selected state, explicitly roll back every modified field, and repeat baseline
checks. Artifact labels cannot be overwritten. A failed phase retains its samples
with `complete: false`; it is not successful evidence. `--skip-documents` is only
for resuming an interrupted local import from a previously acknowledged source
prefix; replay the uncertain batch and require the final exact document count.

## Evidence status

Measurements are in progress. No full-corpus RSS result or production rollout is
claimed yet. The populated 27.1 fixture passes the actual migration, idempotence,
stored-payload/consumed-query parity, delist/relist and explicit rollback checks.

The earlier combined sort/display fixture exposed a 27.1 numeric-facet summary
statistic discrepancy after rebuilding numeric fields. More seriously, reads
during a consumed-field rebuild can return different matches and facet choices.
The selected display-only engine test compares complete facet responses,
including numeric summary statistics, before and after migration and rollback.
The full-corpus harness still projects consumed fields explicitly. Existing
upstream upgrade investigations remain separate.

The first full import failed on the local VM root filesystem's disk limit.
The rehearsal moved to dedicated Docker volumes with sufficient space. No
production mutation or OOM occurred in that failed attempt.

A later import stopped at about 2.06 million postings with disk-write errors.
The VM filesystem had roughly 17 GiB in use while its sparse backing image
occupied roughly 71 GiB on the Mac; the guest journal aborted and remounted
read-only. Colima's documented free-block trim recovered host space. This
storage failure is not a measured Typesense memory limit or a successful import.

## Why sort pruning is deferred

A populated 1,000-posting 27.1 clone passed before/after parity for the original
11-field candidate, but a concurrent taxonomy probe differed during the
`has_content` patch. An isolated three-trial replay of that single sort removal
confirmed the result in **two of three trials**:

- The unchanged `is_active:true && has_content:!=false` query normally matched
  **155** documents and temporarily matched **157** while the field was rebuilt.
- Seniority facet counts summed to **29 instead of 28**; locale facets exposed
  **10 values instead of 9**, with counts summing to **166 instead of 164**.
- Final responses matched again after each alteration. No document update was
  performed by the repro; only `has_content`'s sort flag was changed by drop/add.

[Timestamped probes](typesense-index-pruning-2026-09-23/has-content-alter-reads.json).
A clean final comparison therefore does not establish a safe live migration.
The selected rollout leaves all seven consumed numeric/boolean fields intact.
The 13.4% earlier sample allocator-active opportunity remains follow-up work,
requiring a migration that preserves live reads or a separately coordinated
maintenance window. It is not part of this PR's claimed saving.

## Automated validation

For the earlier combined draft at `29cb1cede`, [required Linux CI](https://github.com/colophon-group/jobseek/actions/runs/35924172487)
passed: **13,209 crawler tests**, 46 real-engine crawler tests, web tests/build,
PostgreSQL integration, lint, coverage and image checks. The separate crawler
deployment gate remains blocked by #8648. Local checks additionally passed the
36 web real-engine tests against the pruned schema, 99 focused crawler checks,
and four import-evidence guards.

The broad macOS crawler run was interrupted after Linux CI completed: 12,421
passed, 31 skipped, six failed. Four failures were missing optional AI-client
dependencies and passed after installing the same extras as CI. Two unchanged
runtime tests failed local timing bounds (sampler restart and Go race-test
compilation); the sampler failure repeated locally. Those tests passed in Linux
CI. No claim is made that the full macOS suite passed. The narrowed display-only
revision passes 59 focused unit checks, 46 crawler real-engine tests (including
full facet-response comparison), and 36 web real-engine tests. Its small live
migration probe recorded three reads with no mismatch, error or cutoff; the
full-corpus probe is still required. The narrowed revision at `9959357e5` also
passes [required Linux CI](https://github.com/colophon-group/jobseek/actions/runs/35928175698):
13,211 crawler tests plus 46 crawler real-engine tests, with the remaining required
checks green. The crawler deployment gate remains blocked by the active hold. Later harness
changes require their own checks before acceptance.
