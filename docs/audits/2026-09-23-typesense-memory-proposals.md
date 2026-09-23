# Typesense memory proposals — 2026-09-23

Investigation based on `origin/main` at `479d4734767f6f1e5fe3c740d9dc7b755b34b92f` (see the full SHA in lab
artifacts), in isolated branch `fix-crawler/typesense-memory-audit`. Production
access was read-only. Credentials were read from the primary checkout's ignored
crawler environment file, never copied into the worktree or evidence files.

## Decision and scope

Keep the current machine and pursue schema savings first. On 345,690 real
postings, unused numeric/boolean sort indexes cost 13.4% of baseline active
memory; four unused display-field indexes cost 8.0% in a separate test. Broader
pruning saved 24.0%. These percentages are separate comparisons, not additive.

Fix the stable-order representation before enabling narrowing: the planned
base64 key is both expensive and incorrectly ordered by the engine. Typesense
30.2 has a promising memory result (35.3% lower active memory for the baseline),
but the observed grouped-query slowdown and pruned-schema facet mismatch block
an immediate upgrade. Approximate **display** counts remain a useful query
optimization to evaluate; preserve exact posting counts at the narrowing
eligibility boundary.

For narrowing, the best measured memory candidate is a pair of signed integer
keys **with an explicitly validated UUIDv4 domain**: on 27.1, the broadly pruned
schema plus that key used 426.9 MiB active versus 501.6 MiB baseline, a net 14.9%
reduction while adding the ordering capability. It still needs lossless client
handling, full-dataset validation, and a migration/peak-memory rehearsal.

The user explicitly confirmed that inactive postings contribute to UI counts.
Retain those rows and their existing count/filter behavior. The active-only
experiment below scopes only the **new candidate-order key**, not the posting
collection, historical/year counts, or posting-detail payloads.

This document is a proposal, not a deployment or a stable-order readiness
receipt. Local sample results do not satisfy the production-shaped rollout gate
in [the Typesense runbook](../11-typesense.md#stable-candidate-order-rollout).

## What is actually running

Source: [production snapshot](typesense-memory-2026-09-23/production.json),
2026-09-23 15:42 UTC; local API over SSH, Docker inspect, `/proc`, and cgroup v2.
Counts were queried sequentially while normal production work continued.

| Measurement | Observation |
| --- | ---: |
| Engine | Typesense 27.1, Linux x86_64 |
| Host | 8,127,721,472 bytes RAM; 4 logical CPUs; no swap |
| Container hard limit / reservation | 6 GiB / 5 GiB |
| Process RSS (`smaps_rollup`) | 3,853,889,536 bytes, about 3.59 GiB |
| Allocator allocated / active | 3.09 / 3.51 GiB |
| Cgroup total charge | 5.57 GiB |
| Cgroup anonymous / filesystem cache / kernel | 3.58 / 1.93 / 0.053 GiB |
| Host available memory | about 3.3 GiB during initial inspection |
| Posting documents | 5,526,591 |
| Active / active and visible | 2,344,128 / 2,296,559 |
| Inactive | 3,182,463 (57.6%) |
| Inactive, first seen over 90 days ago | 1,831,014 (33.1% of all documents) |
| First seen over a year ago | zero |
| Other collections | 37,526 locations; 6,023 companies; 562 occupations; 186 technologies; 36 seniorities |
| Retired watchlist collection | empty; no duplicate posting collection |
| Stable-order producer field | absent from live schema and current crawler implementation |

The 4 GB/2-vCPU description in older docs is obsolete. The host already received
the August capacity increase; this investigation does not propose another one.

[Seven-day telemetry](typesense-memory-2026-09-23/prom-7d.json) shows cgroup usage
reaching 6 GiB, approximately 700,000 additional `memory.events max` events,
zero new cgroup OOM/kill events, and a 3.97 GiB allocator-active peak.
The host's available memory reached about 1.95 GiB at its low point. These are
different times and must not be summed as if they were a simultaneous snapshot.
The container has run since August 26. There is real reclaim pressure, but no
measured evidence that its steady index alone occupies the entire 6 GiB.

The [history query](typesense-memory-2026-09-23/prom-history.json) requested 30
days but returned approximately 14 days of retained data. Its six-hour samples
show active memory growing from approximately 3.44 to 3.74 billion bytes. They
are trend samples, not peak measurements. The posting count grew by about 1.79M
since the August 26 investigation, averaging about 64,000/day. Savings buy
headroom; they do not eliminate the need for a longer-term retention/read-path
design.

## Correct the memory measurements first

Typesense 27.1 reads jemalloc `stats.resident`, then publishes `active` under
`typesense_memory_resident_bytes`. The same assignment is present in 30.2.
See [27.1 source](https://github.com/typesense/typesense/blob/v27.1/src/system_metrics.cpp#L44-L55)
and [30.2 source](https://github.com/typesense/typesense/blob/v30.2/src/system_metrics.cpp#L41-L52).
Consequently, the old lab's `resident_bytes` column is another active-memory
measurement, not an independent RSS result. Historical claims about resident
savings based solely on that endpoint need this qualification.

Use allocator allocated/active for index attribution, `/proc/<pid>/smaps_rollup`
for RSS, and cgroup `memory.current`, `memory.stat`, `memory.peak`,
`memory.events`, and pressure for capacity. Jemalloc resident is itself distinct
from OS RSS. `retained_bytes` is retained virtual address space; the observed
6.44 GiB is not another 6.44 GiB of physical RAM. Filesystem cache is reclaimable
to varying degrees and cannot all be treated as free guaranteed headroom.
Metric definitions: [Linux cgroup memory controller](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)
and [jemalloc statistics](https://jemalloc.net/jemalloc.3.html).

A [ready-to-file upstream issue](2026-09-23-typesense-resident-metric-issue-draft.md)
is included. It has not been published.

## Fresh benchmark method

Read-only exports of UUID reconciliation buckets `00` through `0f` yielded
345,690 postings, about 6.26% of the collection. The uncompressed SHA-256 is
`8bfcc8810fce8cf236a9d71d363a2ee4bdc808b10bc99050ac85138fcea61eff`.
The sample is 327,415,196 serialized bytes, with 146,924 active documents,
217,558 distinct titles, 4,824 company IDs, 9,576 distinct location names, and
an average of 4.80 expanded location IDs versus 1.09 direct location IDs.
Its largest same-second timestamp tie contains 3,184 documents: dropping the
secondary order is not a safe shortcut for paginated narrowing.

The [local-only harness](../../scripts/typesense-memory-proposals-lab.py) extends
the existing footprint lab. It imports each variant separately, checks import
acceptance, restarts/rebuilds it, waits for stable representative facets, samples
allocator peaks, and replays queries with three warmups and 21 measured runs.
Later runs additionally capture Linux RSS/cgroup breakdown and compare grouped
queries with and without the company-count facet. Stable-order variants are
checked against Python's exact `(timestamp DESC, UUID ASC)` top-250 ordering.

Containers use 6 GiB and four CPUs on a four-vCPU Docker Desktop Linux arm64
VM. Production is x86_64. An existing separate snapshot lab remained running
in that VM, using about 3.8 GiB and little CPU when inspected; see the
[environment observation](typesense-memory-2026-09-23/local-environment.json).
Only one container from this experiment ran at a time, but this was not an
exclusive host. The sample is not a full-cardinality acceptance test,
and latency is susceptible to local contention. No production schema, alias,
documents, config, or feature flag was changed.

All baseline documents in the local lab carry the experimental keys as
stored-only payload so index comparisons share the same payload. The active-only
variant omits the string key for inactive documents. The raw public-job sample
stays outside Git; evidence includes its hash, shape, schemas, and measurements.
The selected UUID prefixes cover only `00`–`0f`, which reduces prefix diversity
in unique string indexes. Neither vocabulary growth nor index cost should be
extrapolated linearly to the full collection. The later boundary fixture tests
ordering across the entire 128-bit UUID range, separately from memory readings.

Reported latency is engine `search_time_ms`, excluding network/UI time. The
earlier corpus uses default typo/drop thresholds; the final confirmation adds
the application's explicit thresholds of 1 and its `max_facet_values: 1`.
Allocator steady readings precede query replay; Linux RSS snapshots follow it.
They measure different phases and should not be subtracted as an allocator
overhead estimate. Container peaks include the preceding import/rebuild phase.

## Initial 27.1 screening results

| 27.1 variant | Active MiB | Allocated MiB | Δ active vs baseline | Peak import / rebuild MiB | Rebuild seconds | UUID order |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `baseline` | 501.6 | 321.9 | +0.0% | 509.1 / 517.0 | 85.9 | not added |
| `response-unindexed` | 461.7 | 287.7 | -8.0% | 475.8 / 483.3 | 73.0 | not added |
| `sort-pruned` | 434.2 | 273.4 | -13.4% | 439.7 / 449.3 | 71.7 | not added |
| `facet-pruned` | 450.1 | 283.1 | -10.3% | 467.8 / 465.1 | 65.7 | not added |
| `combined-pruned` | 381.1 | 232.5 | -24.0% | 401.3 / 403.8 | 87.9 | not added |
| `baseline-string` | 838.4 | 661.6 | +67.2% | 858.0 / 845.6 | 108.2 | FAIL |
| `combined-string` | 728.1 | 580.2 | +45.2% | 732.8 / 742.8 | 63.8 | FAIL |
| `combined-int64` | 426.9 | 278.8 | -14.9% | 435.1 / 446.4 | 80.3 | pass (250 hits) |

Source: [full forward-run artifact](typesense-memory-2026-09-23/lab-27-forward.json).
All figures are for the **345,690-document sample**, not production estimates.
“Peak” in this table is sampled allocator active memory, not RSS or total cgroup
charge. All eight imports accepted every document; all recorded cgroup OOM
counters remained zero.

Ordinary query projections matched in seven variants. `facet-pruned` differed
at the locale top-50 boundary: a count-1 `jv` value was replaced by count-1 `lo`;
shared values/counts and the total of 58 locales matched. The raw mismatch is
retained rather than treating a tied truncation as exact parity.

Latency does not establish production readiness. For example, the baseline /
response-unindexed / sort-pruned / combined-int64 p95s were respectively
28 / 52 / 20 / 32 ms for keyword grouping, 74 / 160 / 69 / 137 ms for taxonomy
facets, and 49 / 94 / 42 / 166 ms for year flow. These runs do not justify claiming
that every optimization passes the 10% latency gate. Further counterbalanced
measurements and the x86_64 full-scale run are required.

## 30.2 comparison

The same sample on 30.2 uses materially less allocator memory, but fails the
latency/semantic screen for an immediate combined rollout.
[Raw 30.2 results](typesense-memory-2026-09-23/lab-30.json).

| Variant | 27.1 active MiB | 30.2 active MiB | 30.2 allocated MiB | 30.2 RSS after queries MiB | 30.2 rebuild seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 501.6 | 324.7 | 257.6 | 473.3 | 74.6 |
| Pruned + current string key | 728.1 | 571.4 | 530.7 | 730.4 | 63.2 |
| Pruned + two int64 halves | 426.9 | 269.2 | 222.8 | 418.4 | 67.5 |

The first baseline comparison is a **35.3% reduction in allocator-active
memory**, not a measured 35.3% RSS reduction. The first 27.1 run did not capture
Linux RSS. All imports succeeded, no local cgroup max/OOM events occurred,
and the sample's 250-hit UUID order still failed for the current string key
and passed for the numeric pair. The separate boundary check below qualifies
the numeric result.

The standard grouped keyword query took 701 / 1,311 / 714 ms median in these
three 30.2 variants. Removing its count facet gave 679 / 960 / 664 ms; this is
not evidence of the hoped-for group-query speedup. In the baseline, grouped
`found` was 681 versus 686 exact companies (-0.73%); `found_docs` was 3,297,
matching the separately requested exact posting count. Counts in pruned
variants were 684 and 682 groups for the same true total, consistent with an
estimate that may change across index builds.

There is also a semantic incompatibility in both 30.2 pruned variants:
`experience_min`'s facet returns `{value: "-1", count: 1620}` where source rows
and all 27.1 variants have `{value: "7", count: 1620}`. The query filters
`experience_min:>=0`; its total 39,693 and other buckets remain unchanged.
The 30.2 baseline is correct. See the
[source-derived ground truth](typesense-memory-2026-09-23/experience-facet-ground-truth.json).
This narrows the problem to a version/schema interaction, but does not yet
isolate the engine's root cause. Approximate counts do not permit incorrect
value labels.

Follow-up on the same day: a [standalone 30.2 reproduction](../../scripts/repro-typesense-int32-facet.sh)
reduces the failure to one int32 facet and two sequential inserts (`-1`, then
`1`). The non-negative filter returns the correct document but labels its facet
`-1`; allowing both values merges their buckets. Both `sort:false` and
`sort:true` reproduce with automatic/exhaustive faceting. `top_values` and an
int64 field return the correct label in these comparison cases. Thus sort
pruning is not required. Source inspection suggests that int32 `-1` collides
with the sentinel for generated facet IDs. See the
[raw responses](typesense-memory-2026-09-23/int32-facet-repro-30.2.json) and
[upstream draft](2026-09-23-typesense-int32-facet-issue-draft.md), which flags the
potential relationship to existing upstream issue #2720. These tiny controls
do not establish a generally safe workaround or resolve the upgrade gate.

## Countercheck and approximate facets on 27.1

The later baseline run measured **504.8 MiB active**, within 0.65% of the
initial 501.6 MiB, with 630.1 MiB RSS after the expanded query corpus. The
corrected base32 key, populated only on active rows and combined with display
unindexing/unused-sort removal (filter-only facets retained), measured
**587.7 MiB active / 707.9 MiB RSS**. It passed both the real sample top-250
order and the 55-UUID boundary fixture, but still adds 16.4% active memory
against this later baseline. It is a correctness candidate, not the cheapest
memory solution.
[Follow-up evidence](typesense-memory-2026-09-23/lab-27-followup.json).

The two-int64 representation passed the real sample, but the full-range
fixture uncovered another boundary: UUIDs with a zero high half map to
`INT64_MIN`, which sorts at the end under default missing-value behavior.
Plain `hi:asc,lo:asc` therefore is not a correct generic 128-bit solution.
Explicit `missing_values:first` also failed, swapping adjacent minimum-boundary
values. Both failures reproduce on 27.1 and 30.2. Do not advertise this encoding
as valid for every UUID. Actual absent keys must still fail the candidate-readiness
gate. [27.1 full-range fixture](typesense-memory-2026-09-23/order-edges-27.json).

All **345,690 sample IDs are UUIDv4**. Restricting the fixture to UUIDv4, including
its low/high boundaries, makes the numeric pair pass on both 27.1 and 30.2;
base64 still fails. The fixed version/variant bits keep these values away from
the problematic integer endpoints. See
[UUIDv4 fixture and sample census](typesense-memory-2026-09-23/order-edges-v4.json).
The [posting schema](../../apps/crawler/src/migrations/versions/0001_initial_schema.py)
defaults IDs with `gen_random_uuid()`, but that is not a constraint forbidding
explicitly supplied non-v4 IDs. Therefore require a **full live UUIDv4 census**,
fail-closed producer validation, and a versioned domain contract before choosing
this option. The sample census does not prove the full collection's domain.

The baseline's taxonomy query, over 144,082 active visible matches, measured:

| Facet sample | Median / p95 ms | Example tradeoff |
| --- | ---: | --- |
| Exact | 75 / 80 | all 58 locale values known |
| 50% | 61 / 63 | rare values/counts can still be inaccurate |
| 10% | 41 / 47 | rare count 1 can become 10; some values disappear |

The lean variant showed the same pattern (exact p95 77 ms versus 47 ms at
10%). Its location `total_values` fell from 9,348 to 3,814 at 10%, and locale
values from 58 to 39. Facet sampling is not the grouped-count estimator's
documented 2% bound. Keep option enumeration/existence checks exact and avoid
using sampled `total_values` to drive completeness or pagination. Consider
sampling broad **display counts** above a threshold, retaining exact queries
for small result sets and checks that determine whether work may proceed.
It is already supported in
[27.1](https://typesense.org/docs/27.1/api/search.html#faceting-parameters).
These tests demonstrate latency savings, not steady-index RAM savings.

## Final version confirmation

Repeating 30.2 after the later 27.1 run gave **325.3 MiB active**, consistent with
324.7 MiB initially. RSS after the expanded query replay was 479.6 MiB, versus
630.1 MiB in the later 27.1 baseline. This corroborates a local memory advantage.
[Confirmation artifact](typesense-memory-2026-09-23/lab-30-confirm.json).

The exact application typo/drop thresholds do **not** resolve grouping latency:

| Keyword query | 27.1 median / p95 ms | 30.2 median / p95 ms |
| --- | ---: | ---: |
| Existing company-count facet | 26 / 28 | 639 / 701 |
| Remove count facet | 19 / 20 | 620 / 628 |
| Remove facet + `group_max_candidates: 10000` | not supported/tested | 1,597 / 1,684 |

The 30.2 group estimate was 685 instead of the exact 686. The explicit group
candidate bound restored 686 but was slower still; it is not the fix in this
sample. `found_docs` stayed 3,297 and ordered groups were preserved when dropping
the facet. The unmodified 30.2 schema's experience facet remained correct.

Recommendation: stage an upgrade to 30.2, but **do not roll it out as the immediate
memory fix**. Reproduce/profile the grouped query on x86_64 and resolve its
latency regression first. Separately test the experience-facet behavior under
each proposed schema reduction. No local result here grants production readiness.


## Implementation proposals

### A. Remove unused posting indexes

Keep `company_name`, `location_names`, `seniority_name`, and `technology_names`
in returned documents, but set them to `index: false`. The current query graph
searches `title`, filters/facets on IDs, and obtains taxonomy/company search
names from their own collections. These four posting fields are presentation
fallbacks; the earlier occupation/name/timestamp reduction is already live and
must not be counted again.

Independently test `sort: false` on `is_active`, `has_content`, `seniority_id`,
`experience_min`, `experience_max`, `experience_min_years`, and
`experience_max_years`. These are indexed and implicitly sortable in production,
but current readers do not sort them. Keep `first_seen_at` and `salary_eur`
sortable: the latter is needed by 27.1's numeric range facets.

Facet pruning is a separate experiment. Keep the company grouping facet and
all consumed taxonomy, salary, experience, locale, employment and work-mode
facets. Start with measured changes rather than indiscriminately disabling
every facet used only for filtering.
Beyond the four display fields, the screened facet reduction covers
`reconciliation_bucket`, `is_active`, `has_content`, `experience_min_years`,
`experience_max_years`, and `experience_max`. Their filter indexes remain.

Implementation caveat: `setup_collections` automatically repairs only **index**
drift. Changing `facet` or `sort` in `COLLECTIONS` alone does not migrate the live
field. Add an explicit reviewed migration or extend drift handling, with schema
verification. Keep values stored so readers and reconciliation remain compatible.

### B. Limit or replace the new stable-order index

The planned 22-character string duplicates each posting's unique identity into
a text-search index and a string-sort structure. Shorter serialized text is not
proof of a cheap in-memory index. The lab measures the actual cost separately
from the existing schema.

**The current `uuid-b64lex-v1` encoding fails the actual engine ordering test.**
On 27.1, adding it to the baseline costs 336.9 MiB active memory on the sample,
and the seventh result already differs from canonical UUID ascending order.
At timestamp `1790177898`, UUID prefix `009522a3` should precede `02447efa` and
`058e1180`, but Typesense returns it after both. Its encoded key begins
`--_H9`; the others begin `-1G6` and `-4YW`. Typesense normalizes the sortable
string before indexing it, so raw ASCII ordering is the wrong model.
[27.1 index implementation](https://github.com/typesense/typesense/blob/v27.1/src/index.cpp#L1048-L1066).
The result is stable across the 21 repeats, not a transient rebuild mismatch.

Do not activate that encoding. A fixed-width lowercase base-32 alphabet
`0123456789abcdefghijklmnopqrstuv` uses 26 alphanumeric characters and avoids
this normalization problem. It passed the engine-backed checks above, but
still needs a new key version and full-scale readiness proof. Merely changing
the application-side comparison test is insufficient.

Two additional ways to reduce the cost while preserving the UUID tie-break are:

1. Populate the corrected optional key only on active postings. Candidate reads already
   filter to active, visible postings. This indexes 42.4% of production rows;
   57.6% would not need a candidate sort key. Every activation/reactivation must
   publish the key atomically with active state, and deactivation must actually
   remove any old indexed value. Update producer/reconciliation/readiness
   contracts to prove coverage of all eligible rows and test those transitions.
2. Encode the UUID as two signed `int64` halves, each unsigned half minus
   `2^63`. This mathematical mapping preserves full 128-bit identity order
   without a collision-prone hash, but use the tested **UUIDv4-only contract**
   described above rather than claiming support for every UUID. A migration
   to another UUID version would require reevaluating this proof. It uses all
   three sort slots.
   JavaScript cannot safely round-trip these values as `Number`; retain lossless
   string validation/proof or use an explicit BigInt/lossless JSON boundary.
   Numeric indexes also consume memory; only measured savings justify this
   larger contract change.

The current crawler schema/exporter/reconciler contain no candidate-order
producer even though the runbook describes its future rollout. Implementing the
producer and reconciliation proof is still required after choosing the shape.

The implementation spans the canonical schema and explicit drift migration in
[`typesense_schema.py`](../../apps/crawler/src/typesense_schema.py), document
construction in [`exporter.py`](../../apps/crawler/src/exporter.py), coverage and
payload checks in [`reconciliation.py`](../../apps/crawler/src/reconciliation.py),
and the web's [candidate query contract](../../apps/web/src/lib/search/watchlist-candidate-query.ts)
and [readiness receipt](../../apps/web/src/lib/search/stable-candidate-order-readiness.ts).
Test pagination across large timestamp ties and active/inactive transitions,
not just the encoder's lexicographic comparison.

### C. Evaluate 30.2 and approximate UI counts

30.2 was the latest official release checked during this investigation. Newer
versions improve grouped-query resource use and numeric range filtering; those
release notes are motivation to test, not a memory-savings guarantee.
[v29 release notes](https://github.com/typesense/typesense/releases/tag/v29.0).

The user accepts approximate display counts. The main web/browser search asks
for an exhaustive `company_id` facet solely to read `stats.total_values`. On
27.1, grouped `found` is exact; on v29+ it is approximate. Use that group count
and remove this extra facet request where it has no other consumer. This does
not need to wait for the upgrade: the later 27.1 baseline with the application's
query thresholds measured 26 / 28 ms median/p95 with the facet versus 19 / 20 ms
without it, returning the same 686 companies, 3,297 postings, and ordered groups.
Label/handle the approximate total consistently in pagination after upgrading.
Do not remove the `company_id` facet **index**:
`group_by` still requires it. This primarily reduces query work and temporary
memory, not the whole index's steady footprint.
Limit this change to the grouped keyword-search calls in
[`typesense.ts`](../../apps/web/src/lib/search/typesense.ts) and
[`typesense-browser.ts`](../../apps/web/src/lib/search/typesense-browser.ts).
The company-browse and per-company year-count queries consume individual facet
values/counts; removing those facets would break their current behavior.

Keep `found_docs` or a separate ungrouped exact count for the 10,000-posting
narrowing gate. The existing eligibility contract already distinguishes these
from company counts. Test 9,999 / 10,000 / 10,001 explicitly on the upgrade path;
the user's display-count tolerance does not change that paid-work boundary.

An upgrade also needs export/reconciliation error-line handling, scoped browser
key and multi-search probes, schema setup, snapshot/restore, and client-library
compatibility. v30 migrates synonyms/curations/analytics resources; changes made
to those resources after upgrade do not automatically migrate back on downgrade.
Preserve a pre-upgrade snapshot and test restoration instead of assuming a
binary rollback is enough.
[v30 migration notes](https://github.com/typesense/typesense/releases/tag/v30.0).
Target [30.2](https://github.com/typesense/typesense/releases/tag/v30.2), rather
than 30.0/30.1, when the compatibility gate passes: it includes fixes to numeric
not-equals filtering, scoped multi-search keys, and cache-key separation that
are relevant to this application's read path.

### D. Separate history only with an explicit read-path redesign

An active-only search index could eventually remove full search structures for
3.18M inactive rows. Today all rows are within the supported year horizon;
deleting old inactive rows would change year counts and posting/saved-job reads.
Moving the same fully indexed history to another collection on this machine
does not save its RAM. A useful split needs a lean stored-only archive or
Postgres/R2-backed historical/detail reads, plus exact routing and CDC repair.
That is a larger product/infrastructure change, not the first narrowing unblock.

## Rollout within the existing machine

Choose a measured schema/key combination, then repeat at 5.53M-or-current
cardinality on x86_64 with the existing 6 GiB limit. Include other collections,
normal read concurrency, CDC catch-up, full rebuild, and backup/snapshot peaks.
Set headroom gates before running; a proposed starting point is at least 20%
steady anonymous/RSS headroom and at least 10% cgroup headroom through the tested
peak, with zero OOM/restarts/rejected imports and explicit latency acceptance.
These are proposed acceptance margins, not measured proof that a candidate fits.

Do not build two complete posting indexes side-by-side on production. For a
schema-only change, compare a measured, writer-quiesced in-place migration with
a scheduled rebuild/restore cutover. The August three-field alter reached its
cgroup limit despite intending to reduce indexes, so do not promise a
monotonically decreasing peak. Rehearse the exact migration and rollback first.
Use verified schema and query readiness, not `/health` alone.

After a reviewed memory result, implement/backfill the selected producer,
complete the fresh full reconciliation proof, then issue the readiness receipt.
Keep ordinary schema savings, version upgrade, and feature activation separate
enough to attribute failures and roll back. No production activation is included
in this investigation.

## Options not supported as the immediate fix

- Raising `memory-used-max-percentage` does not reduce indexes; increasing the
  container cap would consume currently reserved OS/backup headroom.
- Swap, periodic restarts, or clearing caches do not remove the steady index.
  The allocator active-minus-allocated gap is about 433 MiB, an attribution
  bound rather than guaranteed reclaimable RSS.
- RocksDB compaction targets disk layout; it is not evidence of index RAM
  savings. The filesystem has roughly 25 GB free.
- Removing ancestor IDs changes geographic/occupation filtering semantics.
- Clearing the empty retired watchlist collection offers negligible savings.
- Upgrading alone does not fix the resident metric assignment verified in 30.2.

See the checked-in JSON evidence for exact byte values and the local harness
for reproducibility. All savings are attributed to their measurement scale;
none are presented as a verified production rollout result.

## Reproducing the local screen

Use Python 3, Docker, curl, gzip, and a separately exported posting JSONL.GZ.
The run captures its source hash, actual collection schemas, engine image digest,
query projections, import/rebuild measurements, and repeat drift. Later artifacts
also include the exact query parameters and OS/cgroup snapshots. The final
harness contains additional query and boundary probes added after the first
screen; each artifact records its own harness hash.

```bash
python3 scripts/typesense-memory-proposals-lab.py /path/to/sample.jsonl.gz \
  --output /tmp/typesense-27-screen.json \
  --variants baseline response-unindexed sort-pruned combined-pruned combined-int64

python3 scripts/typesense-memory-proposals-lab.py /path/to/sample.jsonl.gz \
  --output /tmp/typesense-30-screen.json \
  --image typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110 \
  --variants baseline combined-int64
```

Run these sequentially. The default 27.1 image is also pinned by digest in the
shared lab. No production credentials are required by either command. Both
commands create/delete only their own disposable local containers and temp
sample/index directories. The provided input sample is retained. A future
fresh production export will naturally have a different count/hash; record it
rather than claiming to reproduce this exact dataset.

The `resident_bytes` metric in raw artifacts retains the upstream name and bug;
use `active_bytes` for the allocator comparison. The inherited lab's disk-size
column was zero in this environment and is not used as disk-footprint evidence.
