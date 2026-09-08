# Queue protocol v2 candidate contract

Status: **inactive conformance candidate** for #8227, a bounded child of #7938.

This directory freezes the queue-v2 safety contract before Postgres or worker
integration. `model.py` is the Python reference state machine. The Go package
under `conformance/go/` independently implements the same transitions. Both
consume the generated synthetic corpus in `fixtures/scenarios.json` and must
produce identical canonical JSON bytes and SHA-256 result digests.

The next inactive slice binds that fence to one shared Redis Lua program at
`contracts/queue/v2/redis/lifecycle.lua`. The Python client and tests live
under the same contract tree, outside the crawler wheel and runtime image; the
Go conformance client executes the exact same Lua file. This candidate uses
Redis `TIME` and a bounded monotonic `claim_sequence` high-water field in the
exact-shape route hash. It retains no per-claim ledger. Every transition
atomically checks route, revision, token, lease, and exactly-one lifecycle
indexing. Its only wire outcomes are `accepted`, `fenced`, `not_current`, and
`transport-error`.

All Redis-side numeric values use canonical decimal integers in
`0..9,999,999,999,999`. That 13-digit bound safely round-trips through Redis
Lua, cjson, sorted-set scores, Python, and Go while accommodating epoch
milliseconds through the year 2286. Redis time, caller durations, route and
config revisions, failure counters, record fields, scores, transition replies,
and token components are checked against the same domain. Checked addition
and claim-sequence exhaustion fail before lifecycle mutation.

Nothing here is imported by the production crawler. It does not change queue
ownership, enable a Go worker, or authorize a deployment.

The Python lease handle exposes an `asyncio.Event` that fires on fenced,
not-current, or transport-error outcomes. This is the prototype cancellation
contract: work must stop and must not perform an authoritative write after the
event fires. A later Postgres adapter still has to re-check the fence inside
the write transaction.

The inactive Go conformance client also exposes a bounded lease supervisor.
It derives a per-lease work context and cancels it with the retained first
typed loss cause when a heartbeat is fenced, not current, malformed,
transport-failed, locally expired, or otherwise ambiguous. A claim grant
includes Redis server time, the returned lease deadline, and a local monotonic
timestamp captured before the claim request. That makes local expiry
conservative without comparing Redis and worker wall clocks. Accepted
heartbeats advance the deadline only when their exact TTL arithmetic and
previous-deadline ordering are valid. Parent cancellation stops supervision
without manufacturing a lease loss, and callers must stop and join supervision
after work returns and before attempting a terminal queue transition. Stop
releases the derived context without recording lease loss. The supervisor does
not claim work, schedule jobs, write Postgres, or authorize mutations; active
supervisors must be bounded by the caller's worker capacity.

## Fence identity

Every claim is bound to all of:

- `shard_id`
- `routing_epoch`
- `engine_owner` (`python` or `go`)
- `config_revision`
- a non-empty, unique `claim_token`

Heartbeat, authoritative-write authorization, completion, reschedule, reap,
and failure/dead-letter transitions require the exact current fence. A stale
transition returns `fenced`, does not authorize a write, does not mutate the
snapshot, and does not consume a failure budget. A reaped or rescheduled task
returns to `ready`; its next claim must use a new token.

The modeled `authorize_write` decision is a contract assertion, not a database
implementation. A later Postgres adapter must compare the same fence in the
authoritative transaction. Checking it before a separate write is not
equivalent.

## Lifecycle and conservation

Each configured task must occupy exactly one lifecycle record:

- `ready`
- `inflight`
- `dead_letter`
- `terminal`

The offline auditor reports deterministic, sorted violations for loss,
duplication, a lifecycle record with no configuration, shard/epoch/owner/config
drift, reused claim tokens, and invalid inflight/non-inflight shapes. Auditing
does not repair or mutate the supplied snapshot.

An expired lease is reapable when `now >= lease_until`. Reap and explicit
failure increment the budget only after the current fence matches. Reaching
`max_failures` moves the task to `dead_letter`; otherwise it returns to
`ready`.

## Corpus

The corpus is synthetic, deterministic, offline, and contains no credentials,
production identifiers, network origins, or timestamps derived from wall
clock time. It includes:

- successful claim, heartbeat, write authorization, completion, and
  reschedule;
- token rotation after reschedule and reap;
- stale token, epoch, owner, and config-revision rejection;
- stale failure-budget protection and dead-letter behavior;
- lease-expiry and global token-uniqueness boundaries;
- every conservation violation class listed above.

Generate or verify it from `apps/crawler/`:

```bash
uv run python contracts/queue/v2/tools/generate_corpus.py
uv run python contracts/queue/v2/tools/generate_corpus.py --check
uv run pytest -q contracts/queue/v2/conformance/python
uv run pytest -q contracts/queue/v2/redis/python
cd contracts && go test -race ./queue/v2/... && go vet ./queue/v2/...
```

The fakeredis suite runs by default. Real-Redis tests in both languages are
opt-in and fail closed unless the operator explicitly marks the target as
isolated:

```bash
export QUEUE_V2_REDIS_URL=redis://127.0.0.1:6379/15
export QUEUE_V2_REDIS_ISOLATED=1
uv run pytest -q contracts/queue/v2/redis/python
cd contracts && go test -race ./queue/v2/...
```

Both implementations consume
`contracts/queue/v2/redis/fixtures/lifecycle_scenarios.json`. The shared trace
covers every fence field, heartbeat, completion, reschedule, expiry/reap,
reclaim and stale-token rejection, dead-letter threshold, client restart,
concurrent double-claim, and before/invalid/ambiguous transport failures. Tests
use unique hash-tagged namespaces and delete only their seven known keys; they
never call `FLUSHDB`.

The dedicated conformance workflow makes both real-Redis suites non-skippable
against the production-pinned Redis 8 image and requires database 15 to start
and end empty.

## Deferred production work

Later #7938 children still own:

- Postgres mutation predicates and transaction-boundary fault injection;
- bounded admission-before-claim and worker-pool integration;
- mixed-protocol rollout, quiescence, rollback, and epoch rotation;
- rebuild/conservation across scrape fallbacks, learned egress state, circuits,
  strikes, and runtime metadata;
- production Go worker ownership.

Redis process crash semantics, persistence recovery, replication/failover, and
cluster resharding remain unproven and deferred. This candidate is not a
deployment authorization.
