# Redis capacity, cleanup, and recovery

The crawler Redis instance is a derived scheduler/cache layer with a 1 GiB
`maxmemory` limit and `noeviction`. This is intentional: silently evicting a
config hash can strand queued work. The corresponding operator contract is to
keep normal and recovery scenarios below 640 MiB, intervene at 75%, and page at
90% before Redis rejects queue/config writes.

## Production baseline and family budgets

The 2026-08-04 pre-cleanup inventory found 1,579,711 keys and 815,407,768 bytes
used. Of 1,571,408 `scrape:<posting_id>` hashes, only 62,074 were referenced by
a scrape queue, lease, or deadletter. The other 1,509,334 (96.05%) were derived
configs left behind after terminal work. Their sampled mean was 505 bytes and
their estimated footprint was 793,517,802 bytes.

The table records the lifecycle owner and agreed hard family budget. The
six-hour `crawler redis-capacity inspect` snapshot publishes exact key/logical
item counts and sampled byte estimates for every row. A family alerts at 80%
of its key, item, or byte budget.

| Family | Owner | TTL/lifecycle rule | 2026-08-04 keys/items and byte estimate | Budget |
|---|---|---|---:|---:|
| `scrape_config` | scrape scheduler | Persistent only while the ID is in a scrape queue, lease, or deadletter; enqueue and terminal deletion are atomic | 1,571,408 / 1,571,408; 793.5 MB | 600k items; 384 MiB |
| `board_config` | `crawler sync` | One persistent hash per configured board; sync deletes disabled/retired boards | 5,700 / 5,700; 6.2 MB | 10k; 16 MiB |
| `scrape_queue_first` | scrape scheduler | Persistent until claim moves the item to a lease | 6 keys / 6,869 items; 0.7 MB | 200k items; 64 MiB |
| `scrape_queue_recurring` | scrape scheduler | Persistent until claim; reschedule returns it to this family | 385 / 55,227; 8.5 MB | 600k items; 96 MiB |
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
uv run crawler redis-capacity inspect --format json
```

`MEMORY USAGE` is sampled from at most 128 keys per family; counts and ZSET
item cardinalities are exact at SCAN time. Redis `SCAN` is non-blocking and the
inventory intentionally avoids `KEYS`.

## Scenario budget

Local Postgres is the durable authority. On 2026-08-04 it held 403,964 active,
non-null scrape schedules. The largest observed 24-hour discovery count was
55,387 and the trailing seven-day total was 117,122.

| Scenario | Scrape configs/items | Estimated total Redis memory | Decision |
|---|---:|---:|---|
| Normal after orphan cleanup | about 62k | under 96 MiB | Healthy |
| Full scheduler rebuild | about 404k | under 320 MiB | Healthy |
| Full rebuild plus seven-day worker outage | about 521k | under 440 MiB | Below 640 MiB operating budget |
| Family hard ceiling | 600k | under 640 MiB aggregate | Stop growth/repair before continuing |
| Intervention threshold | n/a | 75% of 1 GiB (768 MiB) | High-severity forecast alert |
| Write-rejection threshold | n/a | `maxmemory` (1 GiB) | Critical; `noeviction` rejects writes |

The estimates apply the observed 505-byte config mean plus queue/index and
board-family budgets, not only payload bytes. Recalculate this table if
`maxmemory`, the hash schema, scrape policy, or seven-day discovery maximum
changes materially.

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

Expected: every retained scrape hash is `reachable`, the orphan count settles
below 10,000, no deadletter/queue depth drops unexpectedly, and used memory is
below the 640 MiB operating budget.

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
   `ok`. If no RDB is usable, start an empty Redis with the same 1 GiB
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
   aggregate memory below 640 MiB, and no family over budget. Start workers and
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
- `RedisOrphanScrapeConfigs` detects lifecycle regression above 10,000 orphans.
- `RedisCapacitySnapshotStale` detects an unavailable/stale family inventory.
- `RedisMemoryPressure` remains the critical page at 90%.

At warning level, inspect family growth, dry-run the orphan prune, and verify
queue/deadletter state before applying cleanup. At critical level, pause
high-volume enqueue sources, apply only the bounded reachability-safe prune,
and increase `maxmemory` only after verifying host RAM headroom. Do not switch
to an eviction policy as an incident shortcut: losing config hashes can strand
runnable work.
