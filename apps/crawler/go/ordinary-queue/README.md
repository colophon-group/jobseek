# Native ordinary worker queue foundation

This unselected library speaks the existing `claim_work`, `heartbeat_task`,
`complete_task` and `reschedule_task` Lua ABI. It owns no new queue/control
plane and adds no executable, deployment command or production selection.
The copies are tested byte for byte against `src/lua/`.

The client disables mutation retries, bounds I/O, uses Redis TIME, retains a
known claimed descriptor when configuration loading fails and preserves empty
configuration for existing orphan/reaper handling. The caller must handle
that task/error pair; it must not discard an inflight claim accidentally.
Errors never include connection strings or upstream configuration content.

Local race tests against private Unix Redis processes verify both worker
queues, exact claimed/rescheduled deadlines, heartbeat, completed scrape config
cleanup, earliest deferred monitor repair and the persistent B0 guard. Fixtures
own private directories/processes and never clear a shared Redis database.
`go test -race -count=1 ./...`, `go vet ./...` and `go mod tidy -diff` pass on
local macOS/Redis 8.10.1. A skipped Redis test establishes no integration proof;
Linux CI is not wired for this module yet.

This is a foundation for replacing Python ordinary orchestration. The legacy
Lua identifies leases by kind/domain/ID, without a claim token: a stale claimant
can act on a newer lease for the same identity. These tests do not establish
fenced native database authority. Before production selection, complete native
monitor/detail execution, enrichment/persistence, exclusive profile ownership,
claim/write/settlement fencing, cancellation/recovery/cold reversal, and exact
image/output/queue/resource proof. Reuse existing Go HTTP/API/parser and native
enrichment implementations; preserve every enabled board and the scheduler's
repair/fairness/first-time/rate-limit behavior.
