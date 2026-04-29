# `workspace/lib/` — pure async probe / run / select / feedback

This package lifts the CLI handlers (`ws probe monitor`, `ws probe scraper`,
`ws run monitor`, `ws run scraper`, `ws select monitor`, `ws select scraper`,
`ws feedback`) out of `src/workspace/commands/crawl.py` into pure,
importable async functions.

## Purity contract

Modules in this package **must not** import from:

- `src.workspace.commands.*`
- `src.workspace.cli`
- `src.workspace.output`

These imports are blocked by `apps/crawler/scripts/grep-lib-purity.sh`
(invoked from CI and from `tests/test_lib_purity.py`).  Reasons:

1. The lib must be callable from non-CLI contexts (a long-running
   probe pool, a future Murmur worker, etc.). Importing `out.die`
   would tie any caller into `sys.exit`.
2. Importing `commands/*` would pull in click, defeating the lift.

## Public surface

State / inputs:

- `BoardConfigState` — frozen snapshot of board state (probe / run side)
- `ClaimKV` — protocol for per-claim KV store (select / feedback side)
- `InMemoryClaimKV` — reference impl for tests / ephemeral CLI use
- `WsLibError` (and subclasses) — typed exceptions

Functions:

- `probe_monitor`, `probe_scraper`, `run_monitor`, `run_scraper` — async
- `select_monitor`, `select_scraper` — async; persist `{type, config}`
  under a name in `claim_kv` and update the active pointer
- `feedback` — async; records verdict + per-field ratings under the
  active named config in `claim_kv`

Each function returns a JSON-serializable dataclass. See module docstrings.

## Composition: select → run

`run_monitor` (and `run_scraper`) take a `BoardConfigState` snapshot, not
a `ClaimKV`. The composition pattern — used by both the CLI adapter and
the future HTTP route — is:

1. `select_monitor(claim_kv, monitor_type, name='cfg-1', config={...})`
2. The caller resolves `cfg-1` from `claim_kv` (either via `claim_kv.get('cfg-1')`
   or, for the active config, `claim_kv.get(await claim_kv.get_active())`).
3. The caller builds a `BoardConfigState(monitor_type=..., monitor_config=...,
   board_url=...)` from the slot.
4. `run_monitor(state, config_name='cfg-1')` is invoked with the snapshot.

This keeps the J1 lib signature unchanged (it takes the immutable snapshot,
no late binding to a mutable KV) while letting J2 own the named-config
storage. The CLI adapter in `commands/crawl.py` does steps 1–4 in one
handler; the HTTP route in J5 does the same on a Postgres-backed
`ClaimKV`.

## What stays on the CLI side

- File I/O on `.workspace/<slug>/` (artifact persistence)
- Mutation of `state.WorkspaceState` / `Board` (write-back of run results)
- Registry validation (`get_discoverer`, `get_scraper`)
- Human-readable formatting (`out.info`, `out.warn`, tables)
- `sys.exit` on user-facing failures (catch lib exceptions, `out.die`)

The CLI handlers in `commands/crawl.py` are now thin adapters over this lib.

## Active-config tracking in `ClaimKV`

The active named config (used by `feedback`) is stored under the
reserved sentinel key `ACTIVE_KEY` (= `"__active__"`). Use
`claim_kv.set_active(name)` / `claim_kv.get_active()` rather than
poking the sentinel directly. `list_all()` excludes the sentinel so
iterating "named configs" doesn't pick it up.
