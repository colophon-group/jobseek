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
local macOS/Redis 8.10.1. A skipped Redis test establishes no integration proof.
The prepared `Go ordinary queue contracts` workflow tests both Linux
architectures with required real Redis fixtures; missing Redis fails instead
of skipping. No completed Linux result is claimed until that workflow runs.

This is a foundation for replacing Python ordinary orchestration. The legacy
Lua identifies leases by kind/domain/ID, without a claim token: a stale claimant
can act on a newer lease for the same identity. These tests do not establish
fenced native database authority. Before production selection, complete native
monitor/detail execution, enrichment/persistence, exclusive profile ownership,
claim/write/settlement fencing, cancellation/recovery/cold reversal, and exact
image/output/queue/resource proof. Reuse existing Go HTTP/API/parser and native
enrichment implementations; preserve every enabled board and the scheduler's
repair/fairness/first-time/rate-limit behavior.


## Native ownership continuation

Before selecting a first native profile, extend the existing authority rather
than creating a separate ready queue. The current identity-only lease ABI is
insufficient for native write ownership. Admission must cover all of these
boundaries together:

| Boundary | Required contract | Existing surface |
| --- | --- | --- |
| Claim | Distinct claim generation and immutable owner/profile selection; old Python claimers cannot acquire selected work | `claim_work.lua`, existing worker/config and B0 guard |
| Heartbeat/settlement | Check the same generation atomically; expiry, reaping or retirement makes every old operation fail without changing new state | `heartbeat_task.lua`, `complete_task.lua`, `reschedule_task.lua`, existing Go lease reaper |
| Persistence | Transactional generation/epoch check tied to the claimed posting or board, with stale activation and commit rejected | native B0 store is a reference; posting-only fence cannot guard an entire monitor inventory |
| Rich monitor | Preserve source identity, insert/update/disappearance policy, streamed/truncated inventories, description dedup/R2 state and database-owned deadlines | existing Greenhouse/Lever/Ashby/Workday Go parsers and Python monitor processing |
| Detail | Reuse native enrichment and persistence with the same scheduler, retry, policy, disabled/deleted and never-rescrape semantics | native B0 processor/store and ordinary Python pipeline |
| Cutover | All writers quiesced, exact immutable release, exclusive current ownership and complete supported cold reversal | ADR 006 and current B0 activation/reversal contract |

Do not treat a pre-write Redis read, a heartbeat boolean or a process-local mutex
as a database fence. A Redis claim followed by PostgreSQL activation has a seam:
a retired result must not activate or write merely because it reaches the DB
later. Test the authority ordering before adding native selection.

Next implementation: a bounded owner/generation extension across the existing
claim, reaper and settlement state machine, including legacy caller behavior.
Prove expiry followed by a new claim, stale heartbeat/completion/reschedule,
retirement during activation, cancelled writes, and crash after durable commit
before settlement. Then use the current enabled-profile census to choose the
first already ported HTTP/API family and connect its native monitor/detail
processing. This library remains unselected until those gates pass.
