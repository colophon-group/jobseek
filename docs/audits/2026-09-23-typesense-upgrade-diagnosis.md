# Typesense 27.1 → 30.2 upgrade diagnosis

**Reconfiguration helps, but does not remove the upgrade blocker.** A matched
fixture demonstrates a real 30.2 grouping performance regression, even after
removing exact count facets. Whole-UUID tokenization mitigates it; the int32
facet-label bug has a promising targeted query workaround; approximate group
counts are an intentional API change. The resident metric defect is not itself
a reason to stay on 27.1.

This follow-up separates performance, correctness and API behavior changes.
The [original audit](2026-09-23-typesense-memory-proposals.md) measured lower
steady allocator usage and Linux RSS on 30.2, but much slower keyword grouping.
No production schema, application code or deployment is changed by this work.

## Method

The [diagnostic harness](../../scripts/typesense-upgrade-diagnostics.py) uses the
same 345,690-document sample, including inactive postings, with uncompressed
SHA-256 `8bfcc8810fce8cf236a9d71d363a2ee4bdc808b10bc99050ac85138fcea61eff`.
Each variant uses a separate disposable official 30.2 container, pinned to
`sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110`,
with 6 GiB memory/swap combined and 4 CPUs. It imports the full sample, restarts,
waits for document count and representative facets/allocation to settle, and
runs two warm-up rounds plus nine measured rounds in seeded randomized order.
Query caching is disabled. Timings are server-reported milliseconds; client
wall times and result projections are also retained.

This remains an arm64 sample screen, not the full-scale x86_64 production
acceptance run. Tail latencies in this follow-up are noisy (stock current-query
median 809 ms, p95 3,066 ms); small differences are not reliable speedups.
The earlier repeat was 639/701 ms. Both identify the same large grouping cost.

## Query settings on the existing schema

[Raw results](typesense-memory-2026-09-23/upgrade-baseline.json).
The main query is `software engineer`, filtered to active visible postings,
grouped by company, sorted by relevance and recency, returning 20 groups and
up to 10 postings per group. The ordinary API still finds 3,297 postings.

| Change | Median ms | Result / implication |
| --- | ---: | --- |
| Current query, exact company-count facet | 809 | Reference behavior |
| Remove company-count facet; use approximate group count | 802 | Same ordered groups and hits; does not solve latency |
| Above + lazy filtering | 1,178 | Same groups; slower |
| Above + disable highlighting | 940 | Same groups; no demonstrated gain |
| Above + filter candidates = 1 | 838 | Same groups; no demonstrated gain |
| Return one posting per company | 888 | Changes payload, still slow |
| Return five companies per page | 806 | Changes pagination, still slow |
| Disable prefix and typo matching | 379 | Only 3,026 postings; loses 271 matches and changes ranking |
| Search cutoff = 50 ms | 373 | All nine runs cut off, zero returned groups |
| Sort by recency only | 781 | Changes ranking, still slow |
| Ungrouped exact posting count | 19 | Keyword matching/counting itself is comparatively fast |

The prior `group_max_candidates:10000` experiment restored the exact group
count but took 1,597/1,684 ms median/p95. It increases work; it is not a useful
way to recover the old performance.

## Why grouping is different

[The v29 release](https://github.com/typesense/typesense/releases/tag/v29.0)
changed grouped `found` to an approximation. A maintainer explicitly describes
the [grouping refactor as bounding memory](https://github.com/typesense/typesense/issues/2366#issuecomment-2975612347).
That issue remains open for performance improvements; it does not by itself
prove the cause of our timings.

In the [30.2 implementation](https://github.com/typesense/typesense/blob/d45d46baf3996d1de8bf96a87f375cfb43691560/src/index.cpp#L2500-L2780),
grouped search first selects candidate groups, constructs a new string filter
from the selected group values, then searches again with that filter. The
candidate topster has a minimum size of 250. Returning five groups or one hit
per group therefore does not remove the internal candidate work.

Our collection declares `token_separators: ["-", "/"]`; all sampled company IDs
are 36-character strings containing four hyphens. Field-specific separators
are absent. This splits IDs into multiple tokens in the generated filter.
The field-specific tokenization experiment tests whether that interaction
accounts for the slowdown, without changing title tokenization or stored IDs.

## Company-ID tokenization experiment

[Results](typesense-memory-2026-09-23/upgrade-uuid-atomic.json). The only schema
change is:

```json
{
  "name": "company_id",
  "type": "string",
  "facet": true,
  "symbols_to_index": ["-"],
  "token_separators": ["/"]
}
```

The nonempty separator override removes the collection's hyphen separator for
this field. An empty field-level list falls back to collection settings in
the 30.2 implementation; `[]` is not an effective override here. UUIDs contain
no slash. Hyphens remain part of the indexed token. Field-level settings are
[documented by Typesense](https://typesense.org/docs/30.2/api/collections.html#field-parameters).

| Query | Original schema median/p95 ms | Whole-UUID token median/p95 ms |
| --- | ---: | ---: |
| Current grouped keyword query | 809 / 3,066 | 212 / 232 |
| Remove exact count facet | 802 / 2,169 | 201 / 210 |
| Above + lazy filtering | 1,178 / 1,409 | 213 / 232 |
| Ungrouped posting count | 19 / 30 | 15 / 16 |
| Direct filter over 250 sampled company IDs | 12 / 22 | 4 / 5 |

For the normal grouped queries, both schemas return identical ordered groups,
per-group counts and posting IDs. `found_docs` remains 3,297. Estimated group
totals differ across index builds (688 vs 685, true total 686); this is within
the documented approximation behavior. Both full-schema runs have zero
cgroup max/OOM events. Whole-token active memory is 306.2 MiB before queries
and 311.4 MiB afterwards, versus 316.3/326.9 MiB for the original schema.

This is a substantial configuration improvement, but **not a complete fix**:
201 ms still exceeds the previous 27.1 no-count-facet median of 19 ms by an
order of magnitude. Sequential-run timing noise limits a precise attribution
of the speedup. The experiment does not establish production latency at full
collection scale or the safety of an in-place schema rebuild under the live
memory cap.

## Matched group-key isolation, 27.1 versus 30.2

To separate group-key handling from sequential-run noise and unrelated index
fields, a smaller schema retains the same 345,690 documents, titles, company
UUIDs, timestamps, active/content flags and experience values. It adds two
one-to-one representations of each company: a whole-token string and a dense
positive int32. The three group queries run against **the same collection**,
interleaved over two warm-ups and 15 measured rounds. The identical fixture
and workload then run on pinned 27.1. This is an isolation experiment, not a
proposed replacement posting schema.

[27.1 evidence](typesense-memory-2026-09-23/upgrade-key-types-27.json) ·
[30.2 evidence](typesense-memory-2026-09-23/upgrade-key-types-30.json).

| Group key | 27.1 median ms | 30.2 median ms | 30.2 / 27.1 |
| --- | ---: | ---: | ---: |
| Original split UUID string | 16 | 890 | 55.6× |
| Whole UUID string token | 16 | 248 | 15.5× |
| Equivalent positive int32 key | 16 | 60 | 3.75× |
| Ungrouped exact posting count | 10 | 19 | 1.9× |

All three grouped variants preserve the 20 ordered groups, per-group match
counts and ordered posting IDs, within and across versions. All return
`found_docs:3297`. The numeric key is normalized back to its original UUID in
the stored projections. Its local dense numbering is a diagnostic tool, not
a durable production mapping. All imports accepted 345,690 documents.

The within-collection result confirms that string-key handling and tokenization
are substantial contributors. The remaining numeric-key slowdown shows the
upgrade cost is not entirely explained by the hyphen setting. Source inspection
points to the new two-pass grouping and generated-filter path; identifying
the precise hot function or a safe engine patch still needs profiling. This
is evidence of a performance regression, not proof of an additional correctness
bug. Existing upstream #2366 is an appropriate place to discuss it.

## Correctness and application compatibility

The **int32 facet-label collision is a real correctness bug**. It reproduces
with just `-1`, then `1`, irrespective of the sort index. See the
[complete report and reproduction](2026-09-23-typesense-int32-facet-issue-draft.md).
`facet_strategy:top_values` returns correct labels in that minimal fixture.
In this follow-up it also matches all 30 source-derived experience buckets
over 39,693 matching sample documents exactly (median 23 ms versus 26 ms for
automatic). This is evidence for a targeted workaround, not an engine fix.
Both server and browser `getExperienceHistogram` implementations currently
omit the strategy. Validate it on the pruned full-scale schema and filtered
query corpus before rollout; do not replace every facet strategy globally.

The **resident metric defect is also real**, but predates the upgrade and
does not itself prevent one. Use allocator active/allocated plus OS RSS and
cgroup measurements, not the mislabeled resident metric, for acceptance.

**Approximate group counts are an intended API change**, acceptable for the
display use case, but require an application adjustment if we remove the
exact company facet. Currently `search-page.tsx` determines `hasMore` using
`companies.length < totalCompanies`. An estimated total must not be the
termination condition for fetching pages, or underestimates can hide real
companies. Overestimates can leave `hasMore` true after the last page; the
load-more handler has no independent empty-page termination state. Derive
exhaustion from raw group pages (before enrichment/deduplication), and preserve
exact feed-narrowing completeness checks. The current query still asks for its
exact facet, so this concern arises when adopting the approximation.

The v30 release also changes synonym, curation and analytics APIs. A search of
the crawler/search integration found no calls to those management APIs; they
are not an observed blocker here. A full deployment rehearsal still needs a
snapshot and verification of any server-side resources created outside this
repository.

## Recommended decision

Do not treat 30.2 as a drop-in production upgrade on this evidence. Prepare the
company-ID field override and targeted experience `top_values` change, then
qualify them with representative queries and an explicit full-scale latency
budget. The existing 10% regression gate is not met by any measured 30.2
grouping configuration, even with numeric keys. A schema-field change requires
an actual field reindex/migration; changing the desired schema alone is not a
completed rollout.

Use approximate **display** counts if desired, after decoupling pagination and
completeness decisions. This can remove redundant facet work but does not
solve the measured grouping cost. Keep inactive postings for historical UI
counts. Do not use cutoffs or disable normal matching to disguise the slowdown.

For the remaining blocker, pursue an upstream grouping/filter performance fix
or deliberately qualify a different group-key/data model. A numeric company
key would require a durable mapping and exporter/read-path migration; the
60 ms diagnostic result does not justify introducing that contract casually.
The lower 30.2 memory footprint remains attractive, but it must be weighed
against the actual query latency on the production-sized corpus.
