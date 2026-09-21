# Typesense job-posting footprint investigation

Status: investigation and initial production tuning for [GitHub issue #8033](https://github.com/colophon-group/jobseek/issues/8033), captured 2026-08-26. The schema change described below is selected for rollout by the ordinary crawler deploy; it was not applied directly during this investigation.

## Executive finding

The current 3 GiB cgroup does not have enough demonstrated headroom to hold `job_posting_v1` and a full `job_posting_v2` concurrently. The production process was already at 2,642,509,824 resident bytes (2.46 GiB) after the 2026-08-25 OOM recovery. The first production change therefore removes only the in-memory indexes for `occupation_id`, `occupation_name`, and `last_seen_at`. The values remain stored and returned, while all live occupation filters continue to use the retained ancestor-expanded `occupation_ids` field.

On the 233,729-document production-shaped sample, the exact three-field schema reduced steady resident memory by 19.0–20.1 MB and active bytes attributable to the index by 16.8–19.1 MB across counterbalanced runs. Allocated index bytes fell by 7.9–21.0 MB (2.83–7.35%); the wider allocator range is why the lab records active/resident evidence beside allocated bytes. It produced no stable mismatch in IDs, ordering, groups, counts, or consumed facets, and rebuild time improved by 2.8–16.2%.

Docker Desktop query timings remained order-sensitive. With the candidate first, taxonomy-facet and year-flow p95 were 133 ms versus 108 ms and 82 ms versus 66 ms; the other six families stayed within the 10% regression gate or improved. With baseline first, the candidate improved all eight p95s by 14–66%. The result does not demonstrate a causal latency regression or improvement, so production telemetry remains part of the rollout decision rather than extrapolating from this `arm64` host.

The deploy applies each existing-field change in a separate synchronous PATCH while crawler writers are already stopped. Typesense keeps serving reads, stored document values are preserved, the next live-schema read gates the following field, and setup logs allocator memory before and after the series. Extended client, schema-alter, and SSH timeouts cover the 3.7M-document scan on the fixed 2-vCPU node. This is a bounded first reduction, not proof of the issue's eventual 2.0 GiB steady-RAM target.

More aggressive removal of response, automatically-created sort, and facet indexes has a larger footprint trajectory, but the isolated runs also showed order-sensitive latency results. Those candidates remain experiments, not rollout recommendations. With more machine capacity unavailable, subsequent reductions should use the same one-field, in-place, measured sequence instead of a parallel full-collection rebuild.

The probe also found that Typesense 27.1 can return `/health: {"ok": true}` and the final `num_documents` while rebuilt facet output is still changing. Startup and deployment readiness must therefore require a stable representative query, not health alone.

## Production baseline

Read-only API probes were taken through the crawler host. No production collection, alias, key, document, or configuration was changed.

| Signal | Observed |
| --- | ---: |
| Alias | `job_posting -> job_posting_v1` |
| Documents | 3,736,881 (the exporter remained active, so counts moved slightly) |
| Active documents | 1,450,141 |
| Active and visible (`has_content != false`) | 1,417,191 |
| Any document first seen in the last year | 3,736,881 |
| Visible and first seen in the last year | 3,581,856 |
| Inactive and older than one year | 0 |
| Typesense active bytes | 2,642,509,824 |
| Typesense allocated bytes | 2,429,849,688 |
| Typesense resident bytes | 2,642,509,824 |
| Typesense mapped bytes | 2,730,201,088 |
| Typesense retained bytes | 1,303,670,784 |
| Typesense metadata bytes | 46,664,192 |
| Allocator fragmentation | 0.08 |
| Host/cgroup-visible system memory | 4,005,449,728 total; 3,275,300,864 used; no swap |

The `stats.json` endpoint was intentionally unavailable to the scoped operations key. The baseline therefore uses allocator metrics and client-observed search times, not API-wide traffic percentiles.

### Production-shaped sample

The benchmark sample contains the complete read-only export for 16 UUID reconciliation buckets (`00` through `0f`): 233,729 documents, or 6.26% of the observed collection. Every JSONL line was parsed before import.

| Shape | Sample observation |
| --- | ---: |
| Average serialized document | 936 bytes |
| p95 serialized document | 1,130 bytes |
| Average `source_url` payload | 98 bytes (already unindexed) |
| Average `company_icon` payload | 84 bytes (already unindexed) |
| Average `title` payload | 39 bytes |
| Average expanded `location_ids` length | 4.85 |
| Average direct location IDs / names | 1.12 |
| Average technology IDs | 0.93 |

The prefix buckets are deterministic and production-shaped, but they are not a substitute for the production-scale acceptance run. These initial isolated containers ran as Linux `arm64` under Docker Desktop, while the Hetzner deployment is `x86_64`. Only within-run relative deltas are useful; absolute bytes and timings must not be extrapolated to production. The lab records both host and image architecture so the acceptance run can be repeated at full cardinality under the real cgroup.

## Field-consumer matrix

Legend: Q = `query_by`; F = filter; A = facet; G = group; S = sort; R = returned or directly retrieved by a web reader; C = crawler/exporter/reconciliation contract. “Stored-only candidate” means retain the value in the document with `index: false`.

| Field | Consumers | Current reason / evidence | Investigation disposition |
| --- | --- | --- | --- |
| `id` (implicit) | F, R, C | posting detail and saved-job retrieval; batched state filters; identity and reconciliation | retain implicit index |
| `reconciliation_bucket` | F, C | bounds reconciliation export to 1/256 of the collection | retain index; benchmark facet removal |
| `company_id` | F, A, G, R, C | grouping, counts, company/watchlist filters, snapshot fallback | retain facet/index |
| `company_name` | R, C | saved-job snapshot and company fallback | stored-only candidate |
| `company_slug` | R, C | saved-job URL/company fallback | already stored-only |
| `company_icon` | R, C | saved-job/card fallback | already stored-only |
| `title` | Q, R, C | all keyword ranking and rendered posting titles | retain text index |
| `is_active` | F, R, C | base filter, posting/saved state, reconciliation | retain filter index; benchmark facet removal |
| `has_content` | F, C | hides incomplete postings on supported flows | retain filter index; benchmark facet removal |
| `location_ids` | F, A, R, C | ancestor-expanded hierarchy filtering and posting detail | retain facet/index and order |
| `location_direct_ids` | A, C | direct-tag company location counts | retain facet/index |
| `location_names` | R, C | cards and posting-detail fallback, aligned with leaf IDs | stored-only candidate |
| `location_types` | F, A, R, C | work-mode filtering/facets and rendered locations | retain facet/index and order |
| `location_geo_types` | R, C | card/detail display fallback | already stored-only |
| `occupation_id` | C | canonical leaf value retained in reconciliation; web filters use `occupation_ids` | stored-only candidate; removal needs reconciliation migration |
| `occupation_ids` | F, A, C | ancestor-expanded occupation filters/facets and watchlists | retain facet/index |
| `occupation_name` | C | exported and reconciled; no current web read | stored-only candidate; removal needs reconciliation migration |
| `seniority_id` | F, A, R, C | filters/facets and localized detail lookup | retain facet/index |
| `seniority_name` | R, C | posting-detail fallback if taxonomy lookup misses | stored-only candidate |
| `technology_ids` | F, A, R, C | filters/facets and detail list IDs | retain facet/index and order |
| `technology_names` | R, C | detail list names aligned with IDs | stored-only candidate |
| `employment_type` | F, A, R, C | filters/facets and posting detail | retain facet/index |
| `salary_eur` | F, A, C | normalized filter and range histogram | retain facet and sort index; Typesense 27.1 rejects range facets without sort |
| `salary_min` | R, C | saved-job and posting-detail source amount | already stored-only |
| `salary_max` | R, C | saved-job and posting-detail source amount | already stored-only |
| `salary_currency` | R, C | saved-job and posting-detail currency | already stored-only |
| `salary_period` | R, C | saved-job and posting-detail period | already stored-only |
| `experience_min_years` | F, R, C | precise decimal range-overlap filter and detail | retain filter index; benchmark facet/sort removal |
| `experience_max_years` | F, R, C | precise decimal range-overlap filter and detail | retain filter index; benchmark facet/sort removal |
| `experience_min` | F, A, R, C | compatibility filter and experience histogram | retain facet/index; benchmark sort removal |
| `experience_max` | F, R, C | compatibility range-overlap filter and detail | retain filter index; benchmark facet/sort removal |
| `locales` | F, A, R, C | language preferences, watchlists, description-locale selection | retain facet/index |
| `source_url` | R, C | posting redirect and saved-job snapshot | already stored-only |
| `first_seen_at` | F, S, R, C | historical/year cutoff, freshness ordering, rendered timestamp | retain sort/index and default sorting field |
| `last_seen_at` | C | emitted by exporter but not in the reconciliation payload or a current web projection | stored-only candidate; later consider removing from Typesense |

### Read-path coverage behind the matrix

The matrix was derived from these concrete reader families:

- `apps/web/src/lib/search/typesense.ts`: primary grouped search, active/year counts, browse flows, salary and experience histograms;
- `apps/web/src/lib/search/typesense-filters.ts`: location, occupation, seniority, technology, work mode, employment, salary, experience, and locale filters;
- `apps/web/src/lib/search/typesense-posting-detail.ts`: posting detail, saved-job immutable snapshots, and batched active state;
- `apps/web/src/lib/search/typesense-browser-typeahead.ts` and `typeahead-boost.ts`: posting-backed taxonomy/company typeahead counts;
- `apps/web/src/lib/services/company.ts`: company grouping, company posting lists, and per-company facets;
- `apps/web/src/lib/services/taxonomy.ts` and `locations.ts`: taxonomy/location counts and hierarchical facets;
- `apps/web/src/lib/services/watchlists.ts`: public/private watchlist counts and ordered posting results;
- `apps/web/src/lib/actions/preferences.ts`: locale facet values;
- `apps/crawler/src/exporter.py`: complete document construction and CDC upsert;
- `apps/crawler/src/reconciliation.py`: partition filter plus bounded payload parity;
- `apps/crawler/src/repair_relisted_cdc.py`, `sync.py`, and `taxonomy_readiness.py`: repair/setup/readiness interactions with the collection.

## Reproducible isolated lab

`scripts/typesense-footprint-lab.py` is deliberately local-only. It binds disposable Typesense containers to `127.0.0.1`, uses the production 27.1 image pinned by digest, reads the canonical schema through the AST, and never contains production connection details. It provides these independent variants:

- `baseline`;
- `response-unindexed` — unindex the seven stored response/compatibility fields in the matrix;
- `sort-pruned` — explicitly keep sort indexes only for `first_seen_at` and `salary_eur`;
- `facet-pruned` — keep facet indexes only for observed facet/group consumers;
- `combined-pruned` — combine all three for interaction measurement; the name does not imply rollout safety.

Example:

```bash
python3 scripts/typesense-footprint-lab.py benchmark sample.jsonl.gz \
  --variants baseline response-unindexed sort-pruned facet-pruned combined-pruned \
  --response-fields company_name last_seen_at location_names occupation_id \
    occupation_name seniority_name technology_names \
  --baseline-indexed-fields occupation_id occupation_name last_seen_at \
  --output /tmp/typesense-footprint.json >/dev/null
```

The response variant can be reduced to a one-field experiment without editing
the canonical schema:

```bash
python3 scripts/typesense-footprint-lab.py benchmark sample.jsonl.gz \
  --variants response-unindexed baseline \
  --response-fields company_name \
  --output /tmp/typesense-company-name.json >/dev/null
```

After an optimization has entered the canonical schema, reconstruct the actual
pre-change production baseline explicitly. The lab rejects an A/B run that
would otherwise compare an already-unindexed field with itself:

```bash
python3 scripts/typesense-footprint-lab.py benchmark sample.jsonl.gz \
  --variants response-unindexed baseline \
  --response-fields occupation_id occupation_name last_seen_at \
  --baseline-indexed-fields occupation_id occupation_name last_seen_at \
  --output /tmp/typesense-initial-production-tuning.json >/dev/null
```

For each variant the lab records empty, post-import, steady, import-peak, and rebuild-peak allocator metrics; clean-stop data-directory size; import throughput; semantic rebuild time; and min/median/p95/max client-observed latency. It warms the representative queries, compares only user-consumed projections, and checks IDs, ordering, groups, counts, and facet counts.

The restart gate intentionally requires all of the following:

1. `/health` is OK;
2. `num_documents` reaches the imported cardinality;
3. a filter plus exhaustive facet probe returns an identical projection with stable allocator use for three consecutive probes.

This third condition was added after local 27.1 restarts exposed health/count readiness before facet stability.

## Candidate evidence and trajectories

### 1. Unindex stored response fields first

The selected production subset is `occupation_id`, `occupation_name`, and `last_seen_at`. Repository-wide consumer tracing found no live search, filter, facet, group, sort, or web-reader use for them. `occupation_id` and `occupation_name` remain stored for exporter/reconciliation compatibility; `last_seen_at` remains stored for diagnostics. The E2E suite verifies both that the three fields are non-indexed and that an in-place `index: true` to `index: false` alter preserves the directly retrieved value.

The exact subset saved 19.0–20.1 MB of steady sample resident memory, 16.8–19.1 MB of active index memory, and 2.83–7.35% of allocated index bytes while preserving all consumed semantics. Rebuild time improved in both orders. The candidate-first run crossed the p95 percentage gate for taxonomy facets and year flow; the baseline-first run made the candidate faster in every family. This small subset is promoted because the fields are absent from the query graph, the payload remains compatible, both memory directions improved, and the rollout sheds rather than duplicates an index. The timing reversal is retained as an explicit production-monitoring requirement, not hidden by averaging the two orders.

The broader seven-field experiment below is retained as evidence but is **not** the production schema in this change.

This is the lowest-semantic-risk schema trajectory. Typesense still stores and returns `company_name`, `location_names`, `occupation_id`, `occupation_name`, `seniority_name`, `technology_names`, and `last_seen_at`, but does not build search/filter/facet/sort structures for them. “Low semantic risk” does not mean the candidate currently passes the performance gate.

Across the two execution orders, this candidate saved 35,155,424–38,428,424 allocator bytes attributable to the index (12.64–13.64%) and 40–53 MB of steady resident memory. Peak import resident memory fell by 29–42 MB and peak rebuild resident memory by 42–47 MB. The corrected corpus preserved found counts, hit/group IDs, and consumed facet values.

It does **not** pass the isolated p95 gate. With the candidate executed first, experience, keyword, salary, and year-flow p95 regressed by 23.5%, 20.8%, 35.3%, and 53.1%, respectively; four other families were within 10% or improved. The opposite order also made the candidate slower across most families. Rebuild results reversed with execution order (+11.0% when second; -21.2% when first), demonstrating that the single-host duration signal is too noisy. Query regressions remain unexplained and must be narrowed by unindexing one field at a time on production architecture.

Trajectory: observe the three per-field production alters and the logged allocator delta first. Only then promote another stored-only field, one at a time, after counterbalanced isolated runs. Do not create `job_posting_v2` on the current node: the live collection already consumes too much of the 3 GiB cgroup to safely coexist with a second full index.

### 2. Remove accidental sort indexes independently

Typesense creates sort structures for numerical fields unless explicitly disabled. Current read paths sort only `first_seen_at`; `salary_eur` must also remain sortable because Typesense 27.1 range facets require it. Boolean, taxonomy ID, experience, and timestamp fields not in those two sets should be measured with `sort: false`.

A 58,502-document screening run saved 2.80% of allocator bytes attributable to the index and preserved consumed result parity, but five query families exceeded the p95 gate (including a noisy 367% year-flow result). This is too small and order-sensitive for a decision, but it rules out assuming sort pruning is free.

Trajectory: test one numerical field at a time, counterbalance order, and prioritize the highest-population fields. It changes no stored payload and should not affect filter semantics, but it still requires the exact corpus and p95 gate.

### 3. Treat facet indexes as performance indexes, not merely response features

Several fields are filtered but never requested in `facet_by`. The aggressive `facet-pruned` candidate tests removing their facet structures. Early combined runs saved substantially more allocator memory, but broad filters/facets exceeded the 10% latency gate. A facet index can accelerate filtering even when its counts are never returned.

On the 58,502-document screening run, facet pruning saved only 1.07% of index allocator bytes. It preserved consumed parity and happened to improve p95 in that execution order; that counterintuitive result must be counterbalanced before interpretation. The combined response/facet/sort candidate saved 14.07% but failed five p95 families. Its only apparent parity mismatch was an unconsumed company facet value tied at count 1; the lab now projects the `total_values` statistic that the main search actually consumes.

Trajectory: attribute the regression field-by-field. Start with response-only fields, then `last_seen_at`, `occupation_id/name`, and fields neither filtered nor grouped. Do not blanket-remove facets from `is_active`, `has_content`, reconciliation, or experience filters without production-scale p95 proof.

### 4. Do not split history yet

Every production document was first seen within the last year. The lower 3.58-million “visible recent” count is caused by `has_content`, not age. A hot/archive split therefore saves no documents today and would duplicate routing and CDC/reconciliation complexity.

Trajectory: defer until retention naturally creates a materially cold partition or the web detail/saved-job source of truth is deliberately moved back to PostgreSQL. Preserve exact year and direct posting behavior first.

### 5. Preserve ancestor arrays

Expanded `location_ids` average 4.85 entries versus 1.12 direct locations, so they are an obvious memory consumer. They also implement hierarchy-free exact filtering, and the web explicitly relies on that contract. Trimming ancestors changes results.

Trajectory: only revisit with an alternative exact representation benchmarked end-to-end. Payload compression or name removal saves disk/network, not the in-memory postings needed for the retained integer filters.

### 6. Make startup readiness semantic

The local pinned 27.1 node returned healthy before the imported collection's facet output stabilized. Production deploy/readiness code that gates solely on `/health` can expose 503s or incomplete search during rebuild even without another OOM.

Trajectory: add an application-specific readiness probe for all aliases and at least one stable filter/facet query. Keep this separate from schema savings so it can ship and roll back independently.

### 7. Continue within the fixed capacity envelope

The low-risk measured saving does not yet demonstrate steady <=2.0 GiB and peak <2.5 GiB. The production baseline also lacks swap. Since a larger node is unavailable for now, the rollout must avoid any plan that requires a second full posting collection or concurrent rebuild.

Trajectory: use monotonic in-place reductions, one field at a time, with writers quiesced and reads online. Treat the post-deploy allocator delta as the new production baseline. Revisit a versioned alias rebuild only when measured headroom can hold both indexes safely.

## Decision gates for the next run

A candidate is promotable only after:

1. the field has no live query/filter/facet/group/sort consumer;
2. a production-shaped isolated run has no stable parity mismatch in IDs, order, groups, found counts, or consumed facets;
3. p95 per query family is checked in counterbalanced runs, with absolute milliseconds reported beside percentages;
4. the change retains stored payloads unless every exporter/reconciler/reader has first migrated;
5. production setup changes only one existing field per synchronous PATCH while writers are stopped;
6. setup records before/after allocator metrics and the deploy fails before writers restart if an alter does not complete;
7. no additional field beyond this initial set is promoted until its production delta and query telemetry are understood.

Disk size is recorded only as an operational signal. It is not used to infer RAM savings; RocksDB compaction/snapshot timing made live directory size too variable for that purpose.

## Production rollout and rollback

The ordinary crawler deploy is the rollout mechanism; this investigation does
not mutate production directly.

1. The deploy transaction stops every crawler-side Typesense writer.
2. `crawler setup-typesense` captures allocator metrics, then alters
   `occupation_id`, `occupation_name`, and `last_seen_at` in schema order, one
   field per synchronous PATCH.
3. Each subsequent field is attempted only after a fresh collection schema
   confirms the preceding change. A failure aborts before `crawler sync` and
   before workers/exporter restart.
4. Setup captures the post-alter allocator metrics. The deploy then runs the
   normal sync and restarts writers.
5. After rollout, compare `typesense.setup.memory_delta` with container memory,
   verify direct retrieval still includes the three stored values, and watch
   representative query latency and exporter/reconciliation errors.

An application rollback does not require a schema rollback: old readers and
writers already treat these values as payload-only and the stored values never
leave the documents. Do not run `setup-typesense` from the pre-change image as
part of a routine rollback, because its old schema would rebuild all three
indexes. If an index must be restored for a future consumer, change one field
back to `index: true` in the current setup code and deploy it through the same
quiesced, measured path.

## Typesense 27.1 references

- [Collections and field schema](https://typesense.org/docs/27.1/api/collections.html): indexed schema fields are held in memory; `index: false` keeps a field stored without its in-memory index; facet and numerical sort settings create additional structures.
- [Search](https://typesense.org/docs/27.1/api/search.html): filter, range facet, exhaustive facet, and faceted `group_by` behavior used by the corpus.
- [Cluster operations](https://typesense.org/docs/27.1/api/cluster-operations.html): allocator metric definitions, health semantics, cache clearing, and RocksDB compaction. Compaction may reduce disk/read latency after frequent writes, but is not a RAM-savings proxy.
