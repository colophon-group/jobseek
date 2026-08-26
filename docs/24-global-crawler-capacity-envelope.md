# Global crawler capacity envelope

Status: analytically rejected candidate for issue [#7936](https://github.com/colophon-group/jobseek/issues/7936), effective 2026-08-26. The minimum fan-out-safe Typesense profile passes the CPU, RSS, and throughput gates but fails the frozen economic envelope. It is not production-equivalent execution evidence, and issue #7936 must remain open.

The machine-readable source of truth is [global-v1.json](../capacity/crawler/global-v1.json). The generated [global-v1-report.json](../capacity/crawler/global-v1-report.json) records its canonical SHA-256 digest. These are offline planning artifacts outside the crawler runtime and deployment path. The local routing artifact is evidence for only the deterministic synthetic ownership harness. Every number below is either a frozen input or generated from that exact spec.

## Evidence boundary

| Classification | Covered here | Not covered |
|---|---|---|
| Analytical | Workload arithmetic, retry amplification, tier sizing, queue age, storage, network, and cost | Real latency, allocator behavior, failover behavior, or vendor performance |
| Measured synthetic | Streaming assignment of 10 million board IDs, weighted task/provider/browser/posting ownership, conservation, recovery, and growth stability | Go, Redis, Postgres, Lightpanda, Typesense, origin, or telemetry throughput |
| Future execution gate | The reproducible run plan and pass/fail thresholds | No production-equivalent run is claimed in this PR |

The evidence JSON includes the run date, base commit, platform, Python version, logical CPU count, physical memory when available, commands, units, spec digest, and its own digest. Capacity inputs are planning assumptions until replaced by dated production observations.

## Frozen workload and payload semantics

| Input | Steady | Simultaneous overload gate |
|---|---:|---:|
| Configured boards | 10,000,000 | 10,000,000 |
| Active postings | 100,000,000 | 100,000,000 |
| Monitor success cycles/hour | 1,000,000 | 2,000,000 arrivals/hour |
| Detail success cycles/hour | 5,000,000 | 10,000,000 arrivals/hour |
| Origin attempts/success | at most 1.05 | at most 1.05 |
| Required success service | 1,666.67/s | 9,600/s during recovery |
| Policy-ready burst | 10 minutes | 60 minutes |

Retries are additional work on the queue, workers, browser pool, failed-attempt database commits, and network. Typesense receives only successful authoritative completions. The 9,600/s recovery service is deliberately greater than the continuing 3,333.33/s overload arrival rate.

HTTP response totals are capped at 16 MiB and browser transfer totals at 64 MiB. These are streamed totals, never resident per-task buffers. The cross-stream contract for [#7937](https://github.com/colophon-group/jobseek/issues/7937) uses the identical max_inline_body_bytes, max_artifact_chunk_bytes, max_http_transfer_bytes, and max_browser_transfer_bytes units and values: 8 MiB, 8 MiB, 16 MiB, and 64 MiB. Ordered chunks are validated for contiguous sequence, per-chunk and total size, digest, and completeness while hashing incrementally. At most one chunk per active task, or a normalized replay payload no larger than 8 MiB, is resident. The analytical RSS envelope includes 32 concurrent worker chunks, 0.25 GiB, and 24 browser chunks, 0.1875 GiB, per shard. A producer that buffers a complete 16 or 64 MiB transfer violates this envelope.

Response size distributions, CPU milliseconds, database commits, browser duration, retry rate, and transfer means are frozen in the spec. The generated monthly origin transfer estimate is 1,561.37 TiB at steady load and includes the 1.05 attempt multiplier.

## SLOs and exact arithmetic

The hard thresholds are:

| Scenario | CPU/RSS/capacity | Ready queue | Total queue | Oldest due | Recovery |
|---|---:|---:|---:|---:|---:|
| Steady | at most 35% CPU/capacity and 55% RSS | 1,000,000 | 11,000,000 | 600 s | none |
| 2x plus simultaneous tier loss | at most 70% | 12,000,000 | 22,000,000 | 2,700 s | shard phases at most 600 s; catch-up at most 2,700 s |
| Complete cell disaster | paired cell at most 70% | 2,250,000 frozen | n/a | 18,000 s | restore at most 10,800 s; authoritative RPO at most 60 s |

The queue-age calculation includes continuing arrivals:

~~~
backlog = arrival_rate * due_burst_seconds
drain_seconds = backlog / (catch_up_service - continuing_arrival_rate)
oldest_due = recovery_seconds + drain_seconds
~~~

At 2x, this gives 12 million ready tasks, 1,914.89 seconds to drain after continuing arrivals, 600 seconds of shard recovery, and 2,514.89 seconds oldest due. It passes the 2,700 second limit. Redis retains one future monitor entry per configured board. The 100 million detail schedule/cursor records remain in partitioned control storage and materialize into Redis only inside the ready horizon; they are not 100 million future Redis entries. Thus the 10 million future monitor entries are counted separately from the 12 million materialized ready entries, yielding the 22 million total.

## Topology and simultaneous loss matrix

There are eight independent data cells. Each cell owns 1,024 of 8,192 logical board partitions through an epoch-fenced owner manifest. The modulo assignment is only the initial manifest seed. Runtime routing always reads the manifest, and growth uses capacity-weighted rendezvous assignment; adding a cell does not modulo-remap existing owners.

The overload gate loses one shard in every listed runtime tier at the same time in the affected data cell, one node in one affected Typesense index shard, plus one global telemetry backend. All percentages below are calculated after those losses and include 2x load and retry work.

| Tier | Provisioned | Lost | Surviving | Maximum modeled capacity |
|---|---:|---:|---:|---:|
| Queue primary groups/cell | 8 | 1 | 7 | 64.969% |
| Worker shards/cell | 7 | 1 | 6 | 62.5% |
| Browser shards/cell | 16 | 1 | 15 | 61.25% |
| Postgres writer groups/cell | 3 | 1 | 2 | 62.5% |
| Telemetry collectors/cell | 3 | 1 | 2 | 62.5% |
| Typesense full-replica nodes/index shard | 15 | 1 | 14 | 41.667% |
| Catalog/CDC/R2 service shards/cell | 3 | 1 | 2 | 50% |
| Manifest/policy routing shards/cell | 3 | 1 | 2 | 25% |
| Private load-balancer instances/cell | 2 | 1 | 1 | 42% |
| Telemetry backends/global | 2 | 1 | 1 | 62.5% |

Each queue primary has two replicas. Each Postgres writer group has a synchronous standby; losing one writer group removes its primary and standby capacity from the calculation. Typesense is 24 independent index shards, three colocated with each data cell. Each index shard has five independent three-node HA replica groups, or 15 full copies. Stateless routing distributes a shard's read requests across those groups; CDC sends every write to every group.

Typesense HA replicas do not pool memory or write throughput. As documented by [Typesense HA](https://typesense.org/docs/guide/high-availability.html), each node holds the shard's complete dataset; extra nodes add read capacity, not data capacity. The 640 GiB global working set is therefore 26.667 GiB on every replica of each index shard, or 41.667% of a 64 GiB node both before and after one-node loss.

Every one of the 4,000 global user searches fans out to all 24 index shards, so each shard receives the full 4,000 search requests/s; sharding does not divide search QPS. With 15 full-replica nodes per shard, this consumes 33.333% of modeled read capacity in steady state. After one node is lost, the 14 survivors consume 35.714%. Search CPU is 1.333 cores/node steady and 1.429 cores/survivor after loss.

Within each three-node HA group, writes sent to any node are forwarded to and serialized by that group's leader, then applied by every replica. Under the 2x gate, every group leader receives all 400 successful upserts/s for its shard, so per-leader write utilization is 20% of the modeled 2,000 upserts/s. Every surviving node reserves 0.8 CPU cores for replicated writes; combined with fan-out search, hottest-node CPU is 27.857% of eight cores after loss. The 15 replicas are the minimum odd three-node-group multiple that keeps steady modeled capacity below 35% without inventing a higher per-node benchmark. Collector and backend lines are separate: critical telemetry is dual-written and either backend reserves capacity for all 50,000 active series and 300 GiB/month of signals.

The checker derives the matrix from topology and workloads, asserts every lost count is exactly one, and fails if any surviving CPU, RSS, session, transaction, series, signal, or modeled throughput percentage exceeds 70%.

## Ownership, policy, and conservation

A canonical board ID hashes to one of 8,192 logical partitions. The epoch-fenced manifest maps that partition to a cell and queue shard. A task carries the manifest epoch; a stale owner cannot commit after fencing.

Recovery is deterministic and constrained to surviving shards in the same cell. The planner considers board count, monitor plus detail task rate, provider family, browser work, and active-posting weight. The full-cardinality harness must keep every recovered dimension at or below 1.32 times its mean. Only the failed shard's partitions may move.

Posting admission is independently partitioned into 8,192 ledgers by the normalized canonical posting URL. That owner performs the uniqueness decision before an authoritative completion, so the same URL discovered through different boards cannot create two completions. Policy and circuit-breaker state is partitioned 4,096 ways by canonical origin or provider-account key. Its epoch owner serializes counters and leases; it is not a process-local cache and there is no singleton global policy service.

Catalog, owner manifests, policy records, posting ledgers, and detail-schedule/downstream cursors are three-copy control metadata. The calculated logical footprint is 83.45 GiB and replicated footprint is 250.34 GiB, below the 256 GiB budget. Peak failed-shard partitions contain at most 3,052 policy keys and 15,259 active posting ownership records at the declared imbalance.

The recovery sequence is fixed:

1. Detect the loss and freeze the affected manifest epoch within 120 seconds.
2. Compare replica watermarks and prove queued, inflight, committed, and dead-letter conservation within 120 seconds.
3. Publish the recovered owner manifest, fence the old epoch, and rebuild leases within 300 seconds.
4. Reserve 60 seconds for resume checks.

No acknowledgement is accepted before the authoritative database completion and owner-ledger transition. Replays use stable task and attempt identifiers. The harness asserts zero lost boards, zero duplicate boards, all 100 million weighted postings, all 6 million cycles/hour, and no unaffected recovery movement. Its 8-to-9-cell growth exercise asserts that only partitions assigned to the new cell move.

## Data, storage, and disaster recovery

Board data is colocated with its board partition. Each cell has three Postgres writer groups with synchronous standbys. Descriptions are content-addressed in R2; database rows retain metadata and object references. CDC, R2 drain, and repair workers run in the cell-service tier. Downstream cursor ownership is partitioned with epoch fencing. Each data cell owns three Typesense index shards, and search fans out through stateless routing shards.

| Store | Calculated payload | Budget |
|---|---:|---:|
| Postgres retained rows and board metadata | 2.738 TiB | 16 TiB primary plus 4 TiB WAL/scratch |
| R2 descriptions | 11.921 TiB | 16 TiB |
| Typesense active index, 15 full copies | 16.764 TiB | 18 TiB replicated |
| Control metadata, three copies | 250.340 GiB | 256 GiB |
| Queue peak, primary plus two replicas | 47.207 GiB | 1 TiB AOF/snapshots |
| Backup copies | two | 16 TiB storage allowance |

The database input is 7,475 bytes per retained posting, based on an approximately 7.3 KiB production relation-size observation dated 2026-08-26. It must be refreshed before hardware purchase with a relation-inclusive query such as:

~~~sql
SELECT sum(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(relname)))
FROM pg_stat_user_tables
WHERE relname IN ('job_posting', 'job_posting_location', 'job_posting_occupation');
~~~

The complete-cell case is a disaster envelope, not live unsafe failover. The lost cell stays fenced until Postgres/WAL and manifest epochs are verified. With a 10,800 second restore, it freezes 2.25 million tasks; a paired cell drains them in 2,872.34 seconds while serving both cells' continuing arrivals. Oldest due is 13,672.34 seconds, below 18,000, and RPO is capped at 60 seconds. Two backup copies and a monthly restore drill are priced.

## Economics

Prices are dated 2026-08-26, exclude VAT, use a planning USD/EUR rate of 0.9, and are inputs rather than vendor commitments. Sources are the [Hetzner 2026 adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/), and [Grafana Cloud pricing](https://grafana.com/pricing/).

| Case | Monthly cost | Board EUR/million | Detail EUR/million | Blended EUR/million |
|---|---:|---:|---:|---:|
| Steady provisioned fleet | €83,266.04 | €14.1650 | €20.2965 | €19.2745 |
| Sustained 2x volume on the provisioned fleet | €84,006.71 | €7.0825 | €10.2511 | €9.7230 |
| Sensitivity case | €143,518.57 | €43.6338 | €44.4283 | €44.2959 |

Unit costs allocate shared pools by their causal driver: worker CPU milliseconds, browser session-seconds, database commits, response bytes, proxy share, or equal successful-cycle share. R2 and Typesense are assigned to detail cycles. The Typesense pool prices all 360 full-replica nodes required by the safe fan-out profile. The steady pool includes €35,028.00 Typesense, €17,726.72 browser, €12,350.40 Postgres, €7,755.44 worker, €2,159.60 cell services/load balancers, €2,063.76 routing, €2,014.08 queues, €917.61 R2, €640 proxy, €581.92 backups, €528.51 telemetry, and €1,500 miscellaneous.

This profile is rejected rather than silently loosening the reviewed limits. Steady cost is €83,266.04 versus the €55,500 ceiling; detail and blended unit costs are €20.2965/€19.2745 versus €13/€13. At 2x, detail and blended costs are €10.2511/€9.7230 versus €6.50/€7.00. The sensitivity case combines 1.25 times infrastructure prices, 0.75 times sustained volume, twice the browser prevalence in compute and transferred bytes, five times proxy price, €8/TiB origin transfer, twice telemetry cost, and 1.5 times backup copies. Its €44.4283 detail and €44.2959 blended unit costs exceed the frozen €32/€34 limits. `--check` therefore fails closed on the monthly steady cost gate. A replacement design needs measured per-shard search capacity, fewer fan-out shards, query aggregation/caching, or a different search engine before this envelope can become a passing candidate.

## Reproduction

From the repository root:

~~~bash
# Expected to exit non-zero: monthly steady cost ceiling exceeded.
python3 scripts/capacity_envelope.py --check
python3 scripts/capacity_envelope.py \
  --write-report capacity/crawler/global-v1-report.json \
  --benchmark-routing 10000000 \
  --write-evidence capacity/crawler/evidence/2026-08-26-local-routing.json
python3 scripts/test_capacity_envelope.py
~~~

The analytical report must reproduce byte-for-byte from the spec. The evidence digest is computed before its own digest field is appended, and the tests independently verify it. The test suite also proves that every resource gate passes and the unchanged economic gate rejects the profile.

## Residual production-equivalent execution gate

The old Python runtime cannot be retired on this analytical PR. The economic rejection must be resolved before a production-equivalent run is treated as a retirement gate. On a future passing candidate Go fleet, with the same spec digest, all of the following remain required:

- Run steady load for at least 24 hours.
- Repeat the simultaneous 2x plus one-loss-in-every-tier drill three times, for at least two hours and at least 24 million successful cycles per drill.
- Exercise real Go workers, queue replicas, Postgres writer groups and standbys, Lightpanda, Typesense, both telemetry paths, service/routing tiers, and network.
- Record per-tier CPU, RSS, throughput, queue-ready and total depth, oldest due, recovery time, retry amplification, database transactions, browser slots, network, and telemetry loss.
- Observe zero lost tasks, zero duplicate authoritative completions, zero origin-policy violations, no unbounded memory window, and at most 1.05 origin attempts per success.
- Pass every steady and simultaneous-loss threshold from the exact checked-in report. Any changed workload, topology, price, or contract limit requires a new revision and digest.

Until a capacity-safe and economically passing replacement profile plus that dated execution evidence exist and are reviewed, this candidate remains rejected and #7936 remains open.
