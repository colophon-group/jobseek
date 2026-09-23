# Typesense keyword-query performance — September 23, 2026

## Change and scope

The grouped keyword readers use Typesense `found` for the company display count
and omit the redundant exhaustive `company_id` count facet. Typesense 27.1 returns
an exact group total; v29+ can estimate it. The index's company facet remains.
Year-count facets (including inactive postings), company-browse facets, matching,
ranking and the exact posting count used by feed narrowing remain unchanged.

Both server and browser readers request one extra raw group with native
`offset`/`limit`. They display only the requested groups and expose `nextOffset`
from the raw page before company enrichment or client deduplication. This avoids
relying on estimated totals and avoids an empty final request for normal page
sizes. The 250-group engine cap permits no lookahead, so that boundary uses one
final probe. Explore persists the offset/exhaustion state across navigation,
retains anonymous truncation and ignores a page returned for superseded filters.
No schema, Typesense version, crawler producer or deployment setting changes.

## Live measurement method

The [read-only replay script](../../scripts/benchmark-typesense-grouped-query.py)
ran against the deployed **27.1, Linux x86_64** server. The first replay began
with **5,552,409 documents** and ended with **5,552,658**; CDC remained active.
The new query includes the extra group, so its cost is included in these timings.
The baseline uses native pagination equivalent to the old page/per-page query.
Neither variant uses the query cache. Full normal posting payloads are fetched;
only counts, timing samples and result hashes are retained in the evidence.

The sequential corpus contains nine keyword/filter/page cases, two warm-ups
and nine measured pairs, in seeded randomized variant order. The projection
compares total companies, exact posting count, ordered group keys, per-group
counts and ordered posting IDs after discarding the new lookahead group.
It also checks pagination exhaustion and 27.1 `found` against the old exhaustive
facet total. Timings are server milliseconds; curl/client wall timings are also
recorded. This is a bounded query replay, not a saturation benchmark.

## Sequential results

| Query | Old median / p95 ms | New median / p95 ms |
| --- | ---: | ---: |
| software | 597 / 628 | 562 / 597 |
| software_page2 | 600 / 690 | 548 / 620 |
| software_page5 | 611 / 676 | 544 / 707 |
| sales | 719 / 794 | 697 / 786 |
| nurse | 419 / 454 | 396 / 448 |
| manager | 476 / 543 | 445 / 499 |
| remote_english | 887 / 976 | 1005 / 1043 |
| salary | 446 / 529 | 434 / 504 |
| zero | 260 / 292 | 263 / 302 |

[Raw sequential results](typesense-query-performance-2026-09-23/sequential.json).
One sales pair changed from 143,228 to 143,229 matching active postings between
requests while the live corpus was growing. All other pairs matched. A separate
15-pair sales replay had zero mismatches and measured 738 → 674 ms median
(965 → 765 ms p95). This is consistent with a live update, not a deterministic
query-shape difference; the engine fixture supplies a static-data parity check.

The first remote-English median regressed, so it was not accepted in isolation.
A 15-pair repeat measured **948 → 923 ms** median (1,058 → 1,110 ms p95), with
no mismatches. A control removing the lookahead group measured **906 → 912 ms**
(1,167 → 1,172 ms p95), also with no mismatches. These runs show timing variation
and do not establish a speedup for this filtered case. The empty-query case is
also effectively unchanged. Small p95 changes are not claimed as improvements.

[Repeat](typesense-query-performance-2026-09-23/repeat.json) ·
[No-lookahead control](typesense-query-performance-2026-09-23/no-lookahead.json).

## Four concurrent requests

Three representative cases were then replayed as bursts of four requests per
variant, two warm-ups plus five measured rounds (20 timed requests per variant
per case). All comparisons matched; none cut off or returned HTTP errors.

| Query | Old median / p95 ms | New median / p95 ms |
| --- | ---: | ---: |
| software | 1149 / 2445 | 561 / 1741 |
| sales | 4695 / 9312 | 3290 / 5938 |
| remote_english | 972.5 / 1146 | 947.5 / 1130 |

[Raw concurrent results](typesense-query-performance-2026-09-23/concurrent.json).
The stronger gains under concurrency support reducing exhaustive facet work.
They are observations on this live workload, not a guarantee for every query.

## Memory observations

A 180-second host sampler read process RSS plus cgroup charge, `memory.stat`
and `memory.events` during the sequential/repeat work. RSS peaked at
**4,181.1 MiB**. The 6,144 MiB cgroup reached **6,143.8 MiB**, including roughly
2,002 MiB of file cache at that sample; anonymous memory was roughly 4,073 MiB.
The cgroup `max` counter rose by 941 during sampling. OOM and OOM-kill counters
remained zero. After the concurrent replay, RSS was 4,041.0 MiB, cgroup charge
5,313.6 MiB, restart count zero and OOM counters still zero.

This demonstrates ongoing cache reclaim at the existing cap; it does not
attribute that pressure to either query variant or prove steady-index RAM
savings. The mislabeled Typesense `resident_bytes` metric is not treated as RSS.
[OS/cgroup evidence](typesense-query-performance-2026-09-23/memory.json).
Index pruning and the 30.2 upgrade remain separate work.

## Regression checks

- Full local web suite: 322 files / 2,690 tests passed. Additional focused tests
  added during the run passed separately (66 provider/helper tests).
- Real local Typesense 27.1 suite: 36 tests passed, including full grouped-page
  order, final-page exhaustion and a non-page-aligned offset against the old
  exhaustive query. Existing historical/year counts and stable-order checks pass.
- Both server and browser tests cover underestimated/overestimated totals,
  orphaned company enrichment, missing/invalid counts, cutoff responses, and
  exact narrowing counts of 9,999 / 10,000 / 10,001.
- UI tests cover raw offsets through deduplication and snapshot restoration,
  explicit exhaustion despite an overestimated total, anonymous truncation,
  and stale page responses after filter changes.
- TypeScript and changed-file ESLint pass; the replay script passes Ruff.

## Reproduction

Use an operator-provided env file without copying credentials into the worktree:

```sh
python3 scripts/benchmark-typesense-grouped-query.py \
  --url https://typesense.colophon-group.org \
  --env-file /path/to/crawler/.env.local --output /tmp/sequential.json

python3 scripts/benchmark-typesense-grouped-query.py \
  --url https://typesense.colophon-group.org \
  --env-file /path/to/crawler/.env.local --output /tmp/concurrent.json \
  --cases software sales remote_english --repeats 5 --concurrency 4
```

For the lookahead isolation control, use `--cases remote_english --repeats 15
--lookahead 0`. The script uses GET requests only. Changing live documents can
produce mismatches; investigate them instead of silently discarding them.
