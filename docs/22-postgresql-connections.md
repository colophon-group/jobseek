# PostgreSQL connection budget

Issue #6631 owns this budget. The local crawler PostgreSQL server remains at
`max_connections=100`; raising it is not part of this design. Both repository
host constructors pin `superuser_reserved_connections=3`, leaving 97 ordinary
slots and a final three-slot superuser recovery path.

## Enforced service inventory

Every pooled crawler service sends a repository-owned `application_name`,
exports its live pool size/idle/in-use gauges, closes inactive connections after
60 seconds, and aborts sessions left idle inside a transaction after 60 seconds.
Direct repository clients set the same ownership and transaction guards but do
not expose a process-local pool gauge. Production Compose sets both pool bounds
explicitly.

| production owner | processes | min each | max each | local maximum | application owner |
|---|---:|---:|---:|---:|---|
| HTTP workers | 3 | 1 | 8 | 24 | `worker-1` through `worker-3` |
| browser worker | 1 | 1 | 6 | 6 | `browser-1` |
| Typesense exporter | 1 | 1 | 4 | 4 | `exporter` |
| R2 drain | 1 | 1 | 6 | 6 | `drain` |
| Murmur Node pool | 1 | 0 (lazy) | 2 | 2 | `murmur-node` |
| Murmur Python children | at most 2 | 0 | 1 each | 2 | `murmur-python` |
| **steady service total** |  | **6** |  | **44** |  |

The Murmur child limit is an invocation semaphore, not an asyncpg pool. Each
child opens at most one short-lived connection and closes it in `finally`, so
the two-child limit makes its aggregate maximum exact.

Scheduled and operator surfaces are separately bounded:

| surface | min | local maximum | overlap rule |
|---|---:|---:|---|
| reconciliation | 0 | 4 | shares the crawler mutation lock |
| Typesense maintenance, CSV sync, currency refresh, location repair | 0 | 4 | one mutation one-off at a time |
| `docker compose run worker-1 ...` operator one-off | 1 | 8 | worst permitted one-off; deploy refuses to overlap it |
| daily labeller | 0 | 2 | read-only; may overlap crawler maintenance |
| host observability sampler | 0 | 1 | sequential `psql` probes |
| pgBackRest | 0 | 2 | `process-max=2` reservation |
| ingress private-path verifier | 0 | 1 | direct, short-lived, may overlap normal services |

Repository operator scripts use either one direct connection or a four-slot
pool, set a `jobseek:operator:*` application name, and fit inside the
eight-slot worst-case Compose one-off row above.

The worst managed non-deploy overlap is therefore 58 connections: 44 steady
services + one 8-slot Compose operator one-off + two labeller slots + two
backup slots + one sampler slot + one ingress verifier. Ten further slots are
reserved for operators (seven ordinary plus the three server-enforced
superuser slots), leaving 32 ordinary shock/incident slots unallocated. The
allocated ceiling is 68/100,
below the 70% steady-state target even when every pool is full.

Do not run an unlabelled direct client or a second maintenance one-off outside
the repository locks. A new production process must be added to this table,
given an application owner and explicit min/max, and included in
`tests/test_postgresql_pool_budget.py` before deployment.

## Deploy overlap

Deploys do not use blue/green PostgreSQL pool overlap. `deploy.sh` refuses
running Compose one-offs, takes the shared mutation lock, and stops all six
crawler asyncpg services before migration or sync. The Murmur sidecar remains
available.

| deploy phase | crawler services | one-shot local clients | Murmur | local maximum |
|---|---:|---:|---:|---:|
| quiesce | 0 | 0 | 4 | 4 |
| Alembic migration (NullPool) | 0 | 1 | 4 | 5 |
| Typesense schema patch | 0 | 0 | 4 | 4 |
| CSV/database sync | 0 | 4 | 4 | 8 |
| new or rolled-back stack healthy | 40 | 0 | 4 | 44 |

Compose replaces containers with the same service names, so old and new pool
generations do not coexist. Rollback follows the same stop-then-start contract.
The independent ingress rollout may add its single direct verification
connection to any healthy-stack phase, for an absolute deployment maximum of
45; it does not create a pool.

## Ownership metrics

Crawler endpoints expose these pool gauges with bounded `role`, `pool`, and
`state` labels:

- `crawler_postgresql_pool_connections` (`open`, `idle`, `in_use`);
- `crawler_postgresql_pool_limit` (`min`, `max`).

The PostgreSQL host sampler also maps the allowlisted `application_name`
values into the bounded aggregate
`jobseek_postgresql_connections_by_owner{owner,state}`. Unknown clients collapse
to `owner="other"`; arbitrary application names never create new series. The
state label is one of `active`, `idle`, `idle_in_transaction`,
`idle_in_transaction_aborted`, `disabled`, or `other`.

The reconciler keeps the session-scoped exporter advisory fence across its
Typesense request, but no longer keeps a database transaction open across that
network I/O. It rereads local truth after the downstream write and fails the
partition closed if a worker committed a conflicting state. The 60-second
`idle_in_transaction_session_timeout` remains a last-resort guard for every
pooled/direct crawler session.

## Seven-day acceptance

Repository acceptance is complete when the configuration/tests land. Production
acceptance still requires seven full days after deployment. Record the deploy
timestamp and evaluate the same uninterrupted range in Grafana/Mimir.

The highest 30-minute steady-state average must remain below 70%:

```promql
max_over_time(
  (
    avg_over_time(jobseek_postgresql_connections[30m])
      / avg_over_time(jobseek_postgresql_max_connections[30m])
  )[7d:5m]
) < 0.70
```

Every sampled peak must remain below the existing 80% alert threshold:

```promql
max_over_time(
  (jobseek_postgresql_connections / jobseek_postgresql_max_connections)[7d:1m]
) < 0.80
```

There must be no sustained idle transaction and every nonzero owner must be
explained:

```promql
max_over_time(
  (sum(jobseek_postgresql_connections_by_owner{state=~"idle_in_transaction.*"}))[7d:1m]
) == 0
```

```promql
max_over_time(
  (sum(jobseek_postgresql_connections_by_owner{owner="other"}))[7d:1m]
)
```

Also confirm `PostgreSQLConnectionsHigh` and `PostgreSQLIdleInTransaction`
never fired, reconciliation completed normally, backup freshness stayed green,
and no connection-rejection errors appeared. A failed condition keeps #6631
open and requires owner-level investigation; it does not authorize increasing
`max_connections`.

## Incident response

Start with ownership and state, not query text:

```promql
sum by (owner, state) (jobseek_postgresql_connections_by_owner)
```

Compare the owning crawler endpoint's `crawler_postgresql_pool_connections`
with its configured `crawler_postgresql_pool_limit`. If `owner="other"` is
nonzero, use an aggregate read-only `pg_stat_activity` query to classify the
client; do not include addresses, credentials, or SQL text in an issue.

For idle transactions, stop or repair the attributed process and verify the
count returns to zero. For total pressure, stop unauthorized one-offs first,
preserve reconciliation/backup/operator reserves, and verify the shared
mutation lock before restarting a bounded owner. Do not raise
`max_connections` or add a proxy without separate memory, CPU, queueing,
timeout, and failure-mode measurements.
