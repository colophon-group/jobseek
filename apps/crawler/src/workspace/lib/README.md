# `workspace/lib/` — pure async probe / run

This package lifts the four CLI handlers
(`ws probe monitor`, `ws probe scraper`, `ws run monitor`, `ws run scraper`)
out of `src/workspace/commands/crawl.py` into pure, importable async
functions.

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

- `BoardConfigState` — frozen snapshot of board state
- `WsLibError` (and subclasses) — typed exceptions
- `probe_monitor`, `probe_scraper`, `run_monitor`, `run_scraper` — async

Each function returns a JSON-serializable dataclass. See module docstrings.

## What stays on the CLI side

- File I/O on `.workspace/<slug>/` (artifact persistence)
- Mutation of `state.WorkspaceState` / `Board` (write-back of run results)
- Human-readable formatting (`out.info`, `out.warn`, tables)
- `sys.exit` on user-facing failures (catch lib exceptions, `out.die`)

The CLI handlers in `commands/crawl.py` are now thin adapters over this lib.
