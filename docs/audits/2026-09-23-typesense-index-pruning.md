# Typesense posting-index pruning — September 23–24, 2026

## Decision

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

## Native full-corpus result: latency gate failed

**Do not roll out this candidate yet.** On native x86_64, the four-field pruning
reduces steady process RSS after restart, but a decimal-experience query becomes
consistently slower. The RSS benefit therefore does not satisfy the requested
no-regression merge condition. PR #9937 remains draft. The open crawler deployment
hold in #8648 independently blocks merge/deployment.

[Native run](https://github.com/colophon-group/jobseek/actions/runs/35939841315) ·
[artifact manifest](typesense-index-pruning-2026-09-23/native-x86_64/manifest.json) ·
[complete derived report](typesense-index-pruning-2026-09-23/native-x86_64/report.json).
The archived JSON.gz files contain measurements and schemas, not posting bodies
or production credentials. The report script also reads this compressed archive
directly. Its measured schema, lab, suite and shared helper exactly match the
candidate files; their SHA-256s are recorded in the manifest.

All 31 operational/semantic phases completed within the **6 GiB, four-CPU**
container. Five full-document fingerprints matched all **5,554,279** source
postings, including **3,192,864 inactive** postings. All consumed results stayed
identical through migration, restart, 500-document delist/relist and rollback.
All **887 reads during forward migration** met the five-second deadline, with
zero mismatch or cutoff; the slowest client observation was 1,726.879 ms.
Setup was idempotent, and rollback restored every original field definition
(the returned field-array order changed). No OOM or OOM kill was observed.
A successful workflow establishes these gates, not latency acceptance.

### RSS and allocator attribution

These are final-20-second medians after the same 16-query workload, except for
allocator values, which are captured once at the end of the settling window.
All values are **MiB**:

| Metric | Original schema | Pruned, restarted | Restored, restarted |
| --- | ---: | ---: | ---: |
| Process RSS | 3,946.6 | 3,635.3 | 3,919.1 |
| Cgroup anonymous memory | 3,903.1 | 3,591.2 | 3,874.8 |
| Sampled query RSS peak | 4,189.5 | 3,861.0 | 4,112.8 |
| Allocator allocated | 3,363.0 | 3,062.7 | 3,364.2 |
| Allocator active | 3,763.7 | 3,453.4 | 3,737.9 |

The pruned process uses **283.8–311.3 MiB less RSS (7.2–7.9%)** than the two
original-schema controls. Anonymous memory falls by a similar amount. Under four
concurrent keyword requests, settled RSS is 3,657.1 MiB versus 3,960.1 MiB before
and 3,939.2 MiB after rollback; the pruned sampled peak is 3,917.5 MiB versus
4,165.1 / 4,178.0 MiB for those controls.

The live alteration alone does **not** produce that saving: matched post-write
RSS was 3,978.9 MiB before migration and 3,982.2 MiB after the subsequent query
workload. Allocator allocated bytes fell by 296.4 MiB during alteration while
allocator active fell only 14.8 MiB. This is consistent with retained/fragmented
allocator pages; it is not evidence that the API's mislabeled resident metric is
OS RSS. The ordinary crawler deploy does not restart Typesense, so merging the
schema change alone must not be advertised as an immediate RSS reduction.

Migration sampled RSS peaked at **4,132.3 MiB**. Import, migration, snapshots and
checkpoint/restart phases approached the 6,144 MiB cgroup cap and recorded
memory-limit events, despite zero OOMs. Forward migration alone recorded 45,439
observed `memory.events.max` increments. File cache remains a material part of
cgroup charge; the RSS reduction is not a matching guaranteed reduction of
`memory.current`. Full per-phase peaks, sample gaps and counter resets are in
the derived report. All native OS sample gaps were below two seconds.

### Latency comparison

Each cell is **median / p95 server ms**, with two warm-ups and 15 timed uncached
requests per sequential case. All result projections are identical. These are
within-run comparisons on the lab host, not predictions of public-endpoint ms.

| Query | Original | Pruned, restarted | Restored, restarted |
| --- | ---: | ---: | ---: |
| `active_location_facets` | 241 / 264 | 243 / 261 | 250 / 277 |
| `combined_filters` | 169 / 181 | 167 / 178 | 170 / 180 |
| `decimal_experience` | 209 / 221 | 241 / 264 | 209 / 216 |
| `experience_overlap` | 176 / 187 | 186 / 197 | 181 / 255 |
| `inactive_history` | 657 / 914 | 668 / 689 | 675 / 706 |
| `keyword_current` | 177 / 187 | 175 / 184 | 185 / 201 |
| `keyword_grouped` | 197 / 206 | 191 / 207 | 206 / 221 |
| `keyword_page5` | 177 / 188 | 174 / 184 | 184 / 190 |
| `keyword_remote` | 240 / 243 | 247 / 262 | 241 / 248 |
| `keyword_sales` | 202 / 213 | 202 / 213 | 216 / 227 |
| `reconciliation_partition` | 1 / 2 | 1 / 1 | 2 / 3 |
| `salary_histogram` | 180 / 200 | 183 / 199 | 195 / 221 |
| `stable_candidates` | 327 / 358 | 341 / 372 | 361 / 418 |
| `taxonomy_facets` | 992 / 1471 | 1003 / 1191 | 1002 / 1077 |
| `year_flow` | 596 / 639 | 627 / 674 | 625 / 654 |
| `zero` | 74 / 78 | 76 / 80 | 74 / 77 |

The decimal-experience query has **465,201 exact matches**. Its median increases
**15.3%** against both controls; p95 increases **19.5–22.2%**. Every pruned timed
sample (236–264 ms) is slower than every first-baseline sample (203–221 ms).
The client-time measurements show the same regression. It remains slow after a
write cycle (three samples: 257 / 267 / 257 ms) and after live rollback
(249 / 258 ms median/p95), then returns to 209 / 216 ms after rebuilding the
restored schema. Before the pruned restart, the same query measured 210 / 231 ms.
The root cause is not established; this is sufficient evidence to reject the
candidate under the stated latency budget, rather than dismiss it as one tail
outlier or loosen the threshold.

This strict probe isolates the two precise numeric fields. The app's
`buildFilterString` also includes legacy-integer and unspecified-experience
fallback branches. The result above therefore does not establish the latency
of that complete UI expression. Follow-up measurements must include the actual
application expression while preserving this failed probe and its original
acceptance threshold.

The initial post-alter screen also flagged active-location facets, combined
filters and the legacy experience histogram; the full report preserves those
failures. After restart, decimal experience is the only sequential case outside
both original-schema controls by more than 10% and 2 ms. Four-concurrency keyword
queries pass the initial screen (60 timed requests per case):

| Query | Original median / p95 | Pruned median / p95 | Restored median / p95 |
| --- | ---: | ---: | ---: |
| `keyword_current` | 242 / 250 | 244 / 255 | 258 / 272 |
| `keyword_remote` | 349.5 / 358 | 350.5 / 379 | 385.5 / 412 |
| `keyword_sales` | 275.5 / 295 | 273 / 286 | 297 / 328 |

### Operational cost and next qualification

Forward migration took **24m 25s**, including readiness and settling; its four
PATCH calls totalled 23m 35s. The current deployment pauses ingestion through
this work. Restart-to-validated-readiness was **252.094 seconds** for the pruned
schema, versus 277.423 and 290.061 seconds for the original-schema controls;
checkpointing and the final settling window are additional. A single-node
restart therefore requires an explicit maintenance/readiness plan. Neither a
production restart nor an ingestion catch-up soak was performed here. The
bounded delist/relist probes restored exact payloads/counts in 802.888 and
805.997 ms before and after pruning; they are not a live exporter catch-up test.

The next bounded experiment is to isolate the four display-field changes and
query execution choices across fresh-process controls, retaining the decimal
range query and the same RSS/latency gates. Do not attribute this timing behavior
to an upstream correctness bug without a separate reproduction. The seven sort
index removals remain deferred for their independent live-result regression.

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

The current deployment quiesces crawler workers, exporter and drain before
`setup-typesense`, and resumes them after schema setup and sync. Consequently,
the measured four-field migration duration also contributes to ingestion pause;
read availability alone does not establish uninterrupted crawl freshness.
The setup client allows a one-hour request and the state-aware patcher has a
two-hour overall deadline; deployment has a three-hour SSH command timeout
inside a six-hour workflow job limit.
Record the native alteration duration and subsequent CDC catch-up explicitly
when judging the rollout window.

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

A [read-only production snapshot](typesense-index-pruning-2026-09-23/production-before.json)
at September 24 01:34 UTC found 5,563,531 live stored postings, 4,028.5 MiB process
RSS, 3,520.9 MiB allocator-allocated and 3,959.3 MiB allocator-active memory.
The cgroup charged 5,852.2 MiB, including 1,773.5 MiB file memory; the host had
3,017.3 MiB available and the write queue was empty. The 6 GiB hard/no-swap limit
was unchanged. This includes all production collections, whereas the lab holds
only postings, and is starting-headroom context rather than a production saving.
A read-only application of the patch planner to that live schema yielded exactly
the four intended display-field rebuilds and no missing-field additions.

The local host is ARM64. Native ARM64 results are useful full-cardinality evidence
but do **not** satisfy the issue's x86_64 production-equivalence gate. An x86 image
under QEMU was rejected for memory acceptance because jemalloc reported different
`MADV_DONTNEED` behavior under emulation. A separate
[native x86_64 run](https://github.com/colophon-group/jobseek/actions/runs/35936721447)
uses a disposable standard GitHub runner, the same complete corpus and a 6 GiB
container limit. Its measured code matches PR head `a4ba256b0`; the temporary
benchmark branch only supplies the manual runner workflow and encrypted-input
transport. That workflow is not a production CI change and must not be merged.
Only aggregate measurement artifacts are uploaded. The encrypted transfer object
and temporary repository secret were deleted after the runner downloaded the
corpus. That first native run failed the source-checksum gate before pruning. Its
measurements are retained as failed import-parity evidence, not accepted
performance results. The [second native run](https://github.com/colophon-group/jobseek/actions/runs/35939841315)
uses the crawler client's JSON serialization and the unchanged source corpus.
Its schema, lab and suite files match PR commit `26ea6874d`; subsequent base/version
updates leave those files unchanged. The second encrypted transfer object and
temporary repository secret were also deleted after its successful download.

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

Summarize a completed lab directory with:

```sh
python3 scripts/typesense-rss-report.py /tmp/pruning-lab > /tmp/pruning-report.json
```

The report uses the median of samples in each phase's final **20 seconds** for
settled RSS and anonymous/file memory, and reports sampled peaks separately.
It preserves the initial p95 screen against the first baseline and also lists
the baseline/pruned/restored measurements for every query. A candidate outside
both controls by more than 10% and 2 ms needs investigation; the control range
is descriptive, not a statistical confidence bound or an automatic acceptance.
Restart phase elapsed time includes its checkpoint and settling window; use
`restart_started_at` through `rebuild_completed_at` for restart-to-validated-
readiness time. Neither value is a guarantee of a production outage duration.

Also repeat `setup` to verify idempotence, restart/measure/fingerprint/snapshot the
selected state, explicitly roll back every modified field, and repeat baseline
checks. Artifact labels cannot be overwritten. A failed phase retains its samples
with `complete: false`; it is not successful evidence. `--skip-documents` is only
for resuming an interrupted local import from a previously acknowledged source
prefix; replay the uncertain batch and require the final exact document count.

## Evidence status

The native full-corpus run completed all 31 semantic/migration phases, but the
latency comparison rejects the candidate for rollout as described above. No
production schema change or RSS saving has been deployed. The smaller populated
27.1 fixture also passes migration, idempotence, stored-payload/consumed-query
parity, delist/relist and explicit rollback checks.

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

The first full post-import fingerprint did not match the source. Recomputing the
source with both JSON Unicode encodings confirmed the original source checksum;
this was not a canonicalization mismatch. A field-by-field comparison of all
5,554,279 documents found the same IDs in the same order and exactly two differing
values, both titles. Each was missing one Chinese character (U+7A0B and U+7545).
No pruning had run.
Both documents round-tripped exactly through separate fresh UTF-8 and ASCII-escaped
imports on 27.1, so no general engine-causation claim is made from this local
incident. The two known document differences account for the entire modular-hash
delta. They were restored from the untouched source corpus in the owned lab only;
the repaired lab's full 5,554,279-document fingerprint exactly matches the original
source. A new checkpointed baseline was established before migration.
The original fingerprints/measurements are retained as failed import-parity evidence.

The native x86_64 run performs its own fresh import and full-source checksum gate;
it does not inherit the local database or its repair history. The first native
run also failed the source checksum after acknowledging and storing all 5,554,279
documents, with no OOM event. Its hash was different from both the source and the
local failed import, so the local disk incident alone cannot explain all failures.
[Native failed-import artifacts](typesense-index-pruning-2026-09-23/native-raw-utf8/manifest.json).

The lab importer now follows the locked `typesense-python` 2.0.0 client, whose
`import_` serializes each dictionary with `json.dumps(doc)`: Unicode is ASCII
escaped on the wire. The original byte-level source hash is retained separately.
A regression test requires exact Unicode values and signed 64-bit candidate keys
after serialization; all six importer guards pass. This does not change the
crawler producer, source corpus, or full-document checksum acceptance criterion.
The second native run passed the complete source checksum after a fresh import
using this serialization, then continued into snapshot/concurrency/CDC phases.
That is evidence for the aligned importer on this corpus; the underlying cause
of the raw UTF-8 discrepancy is still unproven. The completed native migration
and rollback results are summarized above.

The repaired ARM64 run completed all four field changes in 4,048.886 seconds,
but **failed the live-read availability gate**: 52 of 1,336 rotating probes
exceeded the five-second client deadline. All 1,284 successful reads matched the
baseline; none reported a search cutoff. Timeouts affected taxonomy facets (36),
inactive-history counts (13), keyword search (2) and decimal experience (1).
They occurred mainly during the first two field changes (27 and 22 respectively).
No OOM occurred. The largest OS sampling gap was 5.057 seconds, rather than the
long host-suspension gap excluded from the earlier recovery trace.
The matched baseline's slowest taxonomy request already took 4,803.824 ms.
These observations show limited availability margin on that ARM64 VM; they do
not establish the same behavior on production x86_64. The driver stopped with
exit 1 and did not run its restart/rollback sequence. Later settled-state
diagnostics are recorded separately and cannot turn this failed migration into
a passing rehearsal.
[Preserved ARM64 trace and gate result](typesense-index-pruning-2026-09-23/arm64-checkpointed/manifest.json).

After that failure, a diagnostic run matched all 16 consumed query projections
and the complete source fingerprint. A further 120 rotating reads with the same
five-second deadline had no error, mismatch or cutoff. The settled query run
still failed the initial p95 screen for active-location facets (558 → 618 ms)
and sales (345 → 665 ms); it is not accepted ARM64 latency evidence.
Its final-20-second RSS median was 4,003.754 MiB, versus 3,992.145 MiB for the
matched baseline. No ARM64 restart or rollback was performed after the failed
gate; the owned container was stopped after diagnostics. A separate alternating
page-one parameter check (`per_page:11` versus `limit:11, offset:0`) preserved
all results across 192 timed reads on three keyword cases. This checks the lab's
page-one query shape against the app's native-offset spelling; it does not
establish equal latency between different hosts or accept the failed migration.

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
changes require their own checks before acceptance. The complete suite also
passes all 31 phases on 1,000 real postings: identical fingerprints, stable query
responses, three live migration reads without errors/mismatch/cutoff, successful
CDC and explicit rollback, with zero OOM kills. This fixture uses no settling
window and supplies semantic/harness evidence only, not performance acceptance.
[Suite fixture summary](typesense-index-pruning-2026-09-23/suite-smoke.json).

After updating to main's crawler-cohort change, commit `577992acf` passes
[Linux CI](https://github.com/colophon-group/jobseek/actions/runs/35940612930):
13,214 crawler tests (40 skipped), crawler/web Typesense integration, PostgreSQL
integration, web tests/build, lint, coverage and image checks. The Typesense
schema/lab/suite files are unchanged from the native rehearsal's input. The
separate deployment gate remains blocked by #8648.
