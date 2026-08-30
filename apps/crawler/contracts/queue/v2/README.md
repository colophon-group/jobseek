# Queue protocol v2 candidate contract

Status: **inactive conformance candidate** for #8227, a bounded child of #7938.

This directory freezes the first queue-v2 safety slice before Redis, Postgres,
or worker integration. `model.py` is the Python reference state machine. The Go
package under `conformance/go/` independently implements the same transitions.
Both consume the generated synthetic corpus in `fixtures/scenarios.json` and
must produce identical canonical JSON bytes and SHA-256 result digests.

Nothing here is imported by the production crawler. It does not change queue
ownership, enable a Go worker, or authorize a deployment.

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
cd contracts && go test -race ./queue/v2/... && go vet ./queue/v2/...
```

## Deferred production work

Later #7938 children still own:

- Redis Lua/stored-contract compare-and-set transitions;
- Postgres mutation predicates and transaction-boundary fault injection;
- mixed-protocol rollout, quiescence, rollback, and epoch rotation;
- rebuild/conservation across scrape fallbacks, learned egress state, circuits,
  strikes, and runtime metadata;
- production Go worker ownership.
