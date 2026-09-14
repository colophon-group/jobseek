# Redis capacity, cleanup, and recovery

The crawler Redis instance is a derived scheduler/cache layer with a 3 GiB
`maxmemory` limit and `noeviction`. This is intentional: silently evicting a
config hash can strand queued work. The corresponding operator contract is to
keep the sum of family byte ceilings below 75% of `maxmemory`, intervene at
75%, and page at 90% before Redis rejects queue/config writes. The container
has a 4 GiB no-swap cgroup, leaving 1 GiB above the Redis data ceiling for
allocator overhead and RDB copy-on-write.

## Production baseline and family budgets

The 2026-08-04 pre-cleanup inventory found 1,579,711 keys and 815,407,768 bytes
used. Of 1,571,408 `scrape:<posting_id>` hashes, only 62,074 were referenced by
a scrape queue, lease, or deadletter. The other 1,509,334 (96.05%) were derived
configs left behind after terminal work. Their sampled mean was 505 bytes and
their estimated footprint was 793,517,802 bytes. The bounded cleanup restored
the reachability invariant.

By 2026-09-12, legitimate scheduler growth had overtaken the old capacity
model: Redis held 1,757,024 reachable scrape configs, one exact orphan, and
1,758,036 recurring scrape items. It used 1,048,151,584 bytes (97.62% of the
old 1 GiB limit). Config and recurring-queue estimates accounted for nearly
all of it, so orphan pruning was not a remedy. Local Postgres held 2,123,969
active postings, which is the current durable-population upper bound used for
sizing.

The table records the lifecycle owner and agreed hard family budget. The
six-hour `crawler redis-capacity summary` snapshot publishes exact key/logical
item counts and sampled byte estimates for every row without enumerating queue
members. It also publishes a conservative orphan lower bound. A family alerts
at 80% of its key, item, or byte budget. The manual `inspect` action performs
exact reachability classification in bounded-memory batches.

| Family | Owner | TTL/lifecycle rule | 2026-08-04 keys/items and byte estimate | Budget |
|---|---|---|---:|---:|
| `scrape_config` | scrape scheduler | Persistent only while the ID is in a scrape queue, lease, or deadletter; enqueue and terminal deletion are atomic | 1,757,025 / 1,757,025; 830.7 MB | 3m items; 1536 MiB |
| `board_config` | `crawler sync` | One persistent hash per configured board; sync deletes disabled/retired boards | 5,700 / 5,700; 6.2 MB | 10k; 16 MiB |
| `scrape_queue_first` | scrape scheduler | Persistent until claim moves the item to a lease | 6 keys / 6,869 items; 0.7 MB | 200k items; 64 MiB |
| `scrape_queue_recurring` | scrape scheduler | Persistent until claim; reschedule returns it to this family | 1,573 queue keys / 1,758,036 items across scrape queues; 217.0 MB | 3m items; 512 MiB |
| `monitor_queue_first` | monitor scheduler | Persistent until claim | Included in 957 monitor queue keys; <0.2 MB combined | 10k items; 16 MiB |
| `monitor_queue_recurring` | monitor scheduler | Persistent until claim/reschedule | Included in 957 monitor queue keys; <0.2 MB combined | 10k items; 16 MiB |
| `ready_queue` | queue Lua | Six fixed tier indexes rebuilt by enqueue/reschedule/claim | 6 keys; 0.1 MB | 20k domains; 16 MiB |
| `inflight` | lease reaper | Two fixed ZSETs; heartbeat extends scored lease, completion/reschedule/reaper removes it | 2 keys / 12 items; <0.01 MB | 5k items; 8 MiB |
| `inflight_strikes` | lease reaper | Cleared by successful completion; poison work moves to deadletter | 1 key; <0.01 MB | 5k items; 4 MiB |
| `deadletter` | operator recovery | Persistent until explicit `crawler deadletters retry/prune` | 0 material items | 1k items; 4 MiB |
| `delay` | sync/enqueue | One persistent throttle value per active domain; overwritten on enqueue | 1,242; 0.07 MB | 20k; 4 MiB |
| `rate_limit` | claim Lua | Per-domain seconds-long TTL | Ephemeral, negligible | 20k; 4 MiB |
| `host_circuit` | circuit breaker | Failure/open/probe keys expire after the recovery window | Ephemeral, negligible | 30k; 8 MiB |
| `provider_circuit` | circuit breaker | Incident host/open/probe keys expire after the recovery window | Ephemeral, negligible | 20k items; 4 MiB |
| `other` | operator review | Unknown namespaces must be assigned before becoming material | No material family observed | 1k keys; 8 MiB |

Use the current snapshot instead of carrying the baseline forward:

```bash
cd apps/crawler
uv run crawler redis-capacity summary --format json
```

`MEMORY USAGE` is sampled from at most 128 keys per family; counts and ZSET
item cardinalities are exact at SCAN time. Redis `SCAN` is non-blocking and the
scheduled summary intentionally avoids `KEYS` and `ZRANGE`. Its orphan lower
bound subtracts all scrape queue, inflight, and deadletter items from config
keys, plus every entry in the Lightpanda B0 legacy-ownership guard. Because
lease/deadletter/guard counts may include duplicates, the result can understate
orphans but cannot overstate them.

## Scenario budget

Local Postgres is the durable authority. The current model uses 596.55 bytes
per scheduled scrape, measured from the 2026-09-12 config and queue families.
Recalculate it when schedule representation or cardinality changes materially.

| Scenario | Scrape configs/items | Estimated total Redis memory | Decision |
|---|---:|---:|---|
| 2026-09-12 observed | 1.757m | 0.976 GiB | 32.5% after the 3 GiB resize |
| Current active-posting upper bound | 2.124m | 1.180 GiB projected | 39.3% of `maxmemory` |
| Reviewed family hard ceiling | 3m | 1.667 GiB projected | 55.6% of `maxmemory` |
| Sum of all family byte ceilings | n/a | 2220 MiB | 72.3% of `maxmemory`; below intervention |
| Intervention threshold | n/a | 2.25 GiB | High-severity current/forecast alert at 75% |
| Critical pressure threshold | n/a | 2.7 GiB | Critical page at 90% |
| Write-rejection threshold | n/a | `maxmemory` (3 GiB) | `noeviction` rejects writes |

The projections include config and queue representation, not only payload
bytes. The 3m family ceiling leaves about 876,000 schedules above the current
active-posting upper bound and remains below the aggregate operating budget.

## Bounded orphan prune

The command is dry-run by default. It scans no more than the requested count,
classifies each candidate atomically against all four scrape queues, both lease
sets, and both deadletters, and leaves malformed hashes untouched. `--apply`
uses `UNLINK`, capped at 100,000 hashes per invocation.

```bash
cd apps/crawler
uv run crawler redis-capacity prune --max-scanned 100000 --max-delete 100000
uv run crawler redis-capacity prune --max-scanned 100000 --max-delete 100000 --apply
```

Continue with the returned `next_cursor`. When it returns `0`, restart once at
cursor `0` if `delete_budget_exhausted` was true; SCAN over a mutating keyspace
can require a convergence pass. Stop if `missing_domain` is nonzero and inspect
those hashes manually. Never delete `scrape:*` with a wildcard command.

After cleanup:

```bash
uv run crawler redis-capacity inspect --format json
```

Expected: every retained scrape hash is `reachable`, the exact orphan count
settles below 10,000, no deadletter/queue depth drops unexpectedly, and used
memory is below the 75% intervention threshold.

## RDB restore and scheduler rebuild

Redis persists `/data/dump.rdb` in the `deploy_redis-data` Docker volume with
`save 3600 1 300 100 60 10000`; AOF remains disabled. RDB-only durability is
acceptable because local Postgres and CSV configuration are authoritative and
the scheduler now has a bounded, resumable rebuild. The accepted loss window is
up to the last RDB snapshot; recovery rehydrates current state rather than
trying to reproduce expired rate limits or leases.

Recovery order:

1. Stop crawler workers/browser workers so the scheduler is quiescent. Preserve
   the failed volume before replacing anything.
2. Restore a known-good `dump.rdb` into the Redis volume, start Redis, and wait
   for `redis_loading` to return zero and the last background save status to be
   `ok`. If no RDB is usable, start an empty Redis with the same 3 GiB
   `noeviction` configuration.
3. Run `crawler sync` to recreate board hashes, monitor schedules, delays, and
   ready indexes.
4. If an old RDB was restored, run the dry-run orphan classifier and bounded
   prune to remove terminal configs retained by that snapshot.
5. Dry-run and then rebuild durable scrape schedules in UUID-order batches:

   ```bash
   uv run crawler redis-capacity rebuild --limit 10000
   uv run crawler redis-capacity rebuild --limit 10000 --apply
   uv run crawler redis-capacity rebuild --after-id <next_after_id> --limit 10000 --apply
   ```

   Continue until `complete` is true. The enqueue Lua atomically writes each
   config and queue representation, and existing schedules are deduplicated.
6. Run the capacity inventory. Require zero missing configs for reachable IDs,
   aggregate memory below 75% of `maxmemory`, and no family over budget. Start workers and
   watch ready/inflight/deadletter depth plus write errors.

The rebuild path is covered by fakeredis integration tests and must also be
exercised against a disposable Redis container after deployment by overriding
`REDIS_URL`, rebuilding a bounded production-Postgres slice, inspecting it,
and deleting the disposable container. Never flush production Redis to test
recovery.

## Noeviction write-pressure exercise

The CI exercise starts an empty loopback Redis with 8 MiB `maxmemory`,
`noeviction`, and persistence disabled, then runs:

```bash
cd apps/crawler
uv run python ../../scripts/test-redis-noeviction-pressure.py \
  --url redis://127.0.0.1:6380/15
```

The script refuses non-loopback hosts, port 6379, databases below 14, nonempty
databases, non-noeviction policy, and limits above 64 MiB. It proves that writes
are rejected at the limit, an existing sentinel is preserved, evictions remain
zero, and writes recover after bounded `UNLINK` cleanup. The 2026-08-04 local
exercise rejected the 83rd 64 KiB write, preserved the sentinel with zero
evictions, and accepted a new write immediately after cleanup.

## Alerts and operator response

- `RedisMemoryForecastPressure` warns at current or forecast 75% usage, leaving
  15 percentage points before the existing critical 90% alert.
- `RedisKeyFamilyBudgetHigh` identifies the family exceeding 80% of its byte,
  key, or item budget.
- `RedisOrphanScrapeConfigs` detects a conservative lifecycle-regression lower
  bound above 10,000; confirm with the exact manual inventory before cleanup.
- `RedisCapacitySnapshotStale` detects an unavailable/stale family inventory.
- `RedisMemoryPressure` remains the critical page at 90%.

At warning level, inspect family growth, dry-run the orphan prune, and verify
queue/deadletter state before applying cleanup. At critical level, pause
high-volume enqueue sources, apply only the bounded reachability-safe prune,
and increase `maxmemory` only after verifying host RAM headroom. Do not switch
to an eviction policy as an incident shortcut: losing config hashes can strand
runnable work.
