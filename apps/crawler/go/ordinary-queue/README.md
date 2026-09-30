# Native ordinary worker queue foundation

This unselected library extends the existing `claim_work`, `heartbeat_task`,
`complete_task` and `reschedule_task` Lua ABI with opt-in attempt tokens. It
owns no new queue/control plane and adds no executable, deployment command or production selection.
The copies are tested byte for byte against `src/lua/`.

The client disables mutation retries, bounds I/O, uses Redis TIME, retains a
known claimed descriptor when configuration loading fails and preserves empty
configuration for existing orphan/reaper handling. The caller must handle
that task/error pair; it must not discard an inflight claim accidentally.
Errors never include connection strings or upstream configuration content.

Local race tests against private Unix Redis processes verify both worker
queues, exact claimed/rescheduled deadlines, heartbeat, completed scrape config
cleanup, earliest deferred monitor repair and the persistent B0 guard. The
attempt tests verify expiry before reaping, distinct reclaimed tokens, stale
heartbeat/completion/reschedule with unchanged current state, legacy rejection,
duplicate representations, completion/repair/deadletter/orphan/guard cleanup,
corrupt-token-index preflight and cancellation before claim. Fixtures
own private directories/processes and never clear a shared Redis database.
`go test -race -count=1 ./...`, `go vet ./...` and `go mod tidy -diff` pass on
local macOS/Redis 8.10.1. A skipped Redis test establishes no integration proof.
The `Go ordinary queue contracts` workflow tests both Linux architectures
with required real Redis fixtures; missing Redis fails instead of skipping.
The initial foundation passed both architectures at head
`f8ec4e9a4a3b2a8e555eb84795bc2ac16c055c99`, run
[36759941552](https://github.com/colophon-group/jobseek/actions/runs/36759941552).
That result predates the attempt-token extension; new exact-head Linux results
must be recorded before claiming it installed or selecting a worker.

`Claim` retains the legacy ABI; existing Python workers continue tokenless.
`ClaimFenced` allocates a private random 128-bit token, uses Redis TIME inside
the claim and returns the exact stored lease deadline. The same token must
match an unexpired lease in every heartbeat and settlement. The index
`inflight_tokens:<wtype>` extends the existing lease state; ready queues,
fairness and source identity stay unchanged. Legacy calls fail closed while a
tokenized lease exists. Heartbeat accepts an unchanged current token deadline
and never shortens it. Reaping revokes expired tokens before every cleanup
branch. Successful settlement removes the token; repair completion expires
it for the normal reaper. A duplicate ready entry cannot replace a tokenized
lease, and a tokenized claimant cannot replace a legacy inflight lease.

This is a foundation for replacing Python ordinary orchestration. Tokenless
legacy attempts still lack generation identity. The new token proves only
Redis attempt ownership, not profile ownership or a PostgreSQL write grant.
Old deployed scripts unaware of tokens cannot safely coexist with selected
native work: admission requires the updated immutable image and full writer
quiescence, with supported reversal. These tests do not establish fenced native
database authority. Before production selection, complete native
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

Next implementation: bind exclusive profile/epoch selection and the current
attempt to PostgreSQL activation and each transaction. The opt-in Redis attempt
extension above closes stale queue settlement; it does not close the Redis→DB
activation seam. Prove retirement/expiry while DB activation is delayed,
stale/cancelled transactions, and crash after durable commit before settlement.
Then use the current enabled-profile census to choose the first already ported HTTP/API family and connect its native monitor/detail
processing. This library remains unselected until those gates pass.
