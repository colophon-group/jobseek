# Typesense posting-index pruning — September 23, 2026

## Decision under test

Follow-up to [#9919](https://github.com/colophon-group/jobseek/issues/9919).
Remove four unused display-field indexes and seven unused numeric/boolean sort
indexes on Typesense 27.1. Keep the documents, stored display values, all consumed
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
| `is_active`, `has_content` | Active/content filtering and state | Retain index and facet; `sort: false` |
| `seniority_id` | Filtering, facet choices, localized detail lookup | Retain index and facet; `sort: false` |
| `experience_min`, `experience_max` | Legacy overlap filtering; min histogram | Retain index and facet; `sort: false` |
| `experience_min_years`, `experience_max_years` | Decimal overlap filtering and returned experience | Retain index and facet; `sort: false` |
| `first_seen_at` | Freshness order and historical cutoff | Retain index, sort and collection default sort |
| `salary_eur` | Salary filters and numeric range facets | Retain index, facet and sort; 27.1 range facets need the sort structure |
| `candidate_order_hi`, `candidate_order_lo` | Stable feed-narrowing enumeration | Retain numeric sort/index; do not change producer or active-only population |
| `candidate_order_key` | UUID identity verification | Keep stored |
| `id`, `title`, `company_id`, `reconciliation_bucket` | Identity, title search, company grouping/counts, partitioned exports | Unchanged |
| `location_ids`, `location_direct_ids`, `location_types`, `occupation_ids`, `technology_ids`, `employment_type`, `locales` | Filters and consumed facets | Unchanged |
| Remaining stored-only fields | Display, compatibility, source URLs and reconciliation | Unchanged; no repeat claim for previously removed indexes |

No current application query sorts by the seven pruned sort fields or searches/
facets on the four display names. The schemas of the separate company and
taxonomy collections are unchanged. Numeric facet `avg`, `min`, `max` and `sum`
have no application readers; bucket labels/counts and `total_values` do.

## Migration and rollback contract

The canonical schema lives in `apps/crawler/src/typesense_schema.py`.
The existing setup patcher repairs index drift with one drop/add pair per PATCH.
This change additionally repairs `sort: true` to `sort: false` only for the seven
named fields. Unrelated sort/facet/optional differences do not trigger rebuilds.
Setup re-reads the schema between patches; rerunning it is idempotent.

Each PATCH can scan the full collection and temporarily block writes. Rebuilding
one field at a time bounds overlapping work but does not prove a safe RSS peak.
No second full posting collection is created on production.

Capture the complete live schema before rollout. Rollback requires explicitly
drop/adding the eleven original field definitions, one at a time. Reverting the
source commit alone does **not** restore removed sort indexes. The lab's
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
6. Require no OOM kill or unexplained restart, successful snapshot/rebuild,
   delist/relist CDC behavior, and reconciliation parity. Production acceptance
   additionally requires native x86_64 evidence and a post-rollout health/RSS/
   latency/reconciliation soak. The local fixture is not a CDC catch-up soak.

## Reproduction

Use `scripts/typesense-rss-lab.py` with an operator-acquired schema JSON and
JSONL.GZ corpus. No production credentials are accepted by the script.

```sh
python3 scripts/typesense-rss-lab.py init --root /tmp/pruning-lab --schema /private/schema.json
python3 scripts/typesense-rss-lab.py import --root /tmp/pruning-lab --sample /private/postings.jsonl.gz
python3 scripts/typesense-rss-lab.py restart --root /tmp/pruning-lab --label baseline-rebuild
python3 scripts/typesense-rss-lab.py measure --root /tmp/pruning-lab --label baseline
python3 scripts/typesense-rss-lab.py fingerprint --root /tmp/pruning-lab --label baseline-fingerprint
python3 scripts/typesense-rss-lab.py snapshot --root /tmp/pruning-lab --label baseline-snapshot
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

The fixture exposed a 27.1 numeric-facet summary-statistic discrepancy after
rebuilding fields: unused experience/seniority averages and sums can change even
when labels, counts, stored values and `total_values` match. The parity projection
excludes these unused summaries explicitly; it does not claim identical complete
API responses. Existing upstream upgrade investigations remain separate.

The first full import failed on the local VM root filesystem's disk limit.
The rehearsal moved to dedicated Docker volumes with sufficient space. No
production mutation or OOM occurred in that failed attempt.
