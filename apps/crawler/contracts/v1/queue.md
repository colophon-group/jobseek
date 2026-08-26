# Queue contract v1

The Redis Lua scripts in `src/lua/` are the scheduling authority. A Go worker
must load and invoke those scripts rather than reimplementing their
transactions client-side during migration.

## Migration limitation

The current v1 lease member is reusable and has no claim token or routing
epoch. It is safe only while one worker generation owns a cohort. It is not a
cross-generation fencing protocol: a stale worker can continue after lease
loss and race a newer claimant. No mixed Python/Go claim ownership is allowed
until queue protocol v2 adds shard ownership, routing epoch, engine owner,
config revision, unique claim token, and compare-and-set behavior for
heartbeat, completion, reschedule, reaping, and persistence writes.

The v1 runtime `FencingContext` and mandatory 32-byte digest reserve the wire
shape required by #7938. They do not make today's reusable lease member a
strong token. Until #7938 supplies and atomically validates the live token and
epoch, mixed-generation ownership remains blocked.

Required invariants:

- Worker types are `simple` and `browser`; task types are `monitor` and
  `scrape`.
- A task is claimed atomically from a per-domain sorted set and receives an
  `inflight:<worker-type>` lease named `task_type|domain|task_id`.
- A worker heartbeats long tasks with `heartbeat_task.lua`. Losing the lease
  makes the result stale; it must not be committed.
- Completion and reschedule use `complete_task.lua` and `reschedule_task.lua`.
  They also clear the lease. A client-side sequence of Redis commands is not
  equivalent.
- `ready:<worker-type>:<tier>` preserves first-time priority and chooses the
  earliest recurring monitor/scrape score. Monitor wins exact recurring ties.
- `ratelimit:<domain>` is a lower bound on readiness. Queue concurrency never
  overrides per-origin/provider politeness or host/provider circuit state.
- Monitor config lives at `board:<board-id>`; scrape config lives at
  `scrape:<posting-id>`. A claimed task with missing or invalid config follows
  the existing fail-safe path and is never guessed from crawler type.
- Scrape terminal state is guarded by local Postgres `is_active` and
  `next_scrape_at`; Redis scheduling alone is not authority to scrape.
- Reaping is idempotent and bounded. Repeated lease loss reaches the existing
  dead-letter policy rather than spinning indefinitely.

The initial Go conformance suite must run the same Lua scripts against Redis
and replay the Python queue tests before the Go scheduler can own an exclusive
production cohort. Queue v2 fencing and conservation tests are a prerequisite
to changing cohort ownership without a full-fleet quiescence.
