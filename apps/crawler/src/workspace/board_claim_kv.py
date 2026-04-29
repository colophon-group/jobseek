"""``BoardBackedClaimKV`` — adapter mapping the lib ``ClaimKV`` to a ``Board``.

The CLI handlers in ``commands/crawl.py`` use this adapter so the lib's
``select_monitor`` / ``select_scraper`` / ``feedback`` operate on the
on-disk YAML format (per-board ``configs`` dict + ``active_config``
pointer) without the lib needing to know about ``Board``.

Behavior
--------

- :meth:`get` ``name`` returns ``board.configs[name]`` (a *copy* — the
  lib will write back via :meth:`set`, which merges).
- :meth:`set` ``name`` writes the slot back into ``board.configs[name]``
  in a merge-aware way: pre-existing CLI-only fields (``status``,
  ``cost``, ``run``, ``scraper_run``, ``feedback``, etc.) are
  *preserved* if the lib's payload doesn't mention them. The lib only
  ever writes ``monitor_type`` / ``monitor_config`` /
  ``scraper_type`` / ``scraper_config`` / ``feedback``.
- :meth:`get_active` / :meth:`set_active` map onto
  ``board.active_config``.
- :meth:`list_all` returns a copy of ``board.configs``.
- :meth:`clear` empties ``board.configs`` and ``board.active_config``.
- This adapter does NOT call ``save_board`` itself — the CLI handler is
  expected to flush via ``save_board(slug, board)`` at the appropriate
  moment.

The adapter is sync-friendly (the underlying ``Board`` is in memory)
but exposes async methods so it satisfies the lib protocol.
"""

from __future__ import annotations

import copy
from typing import Any


class BoardBackedClaimKV:
    """``ClaimKV`` adapter over a ``Board.configs`` dict.

    Constructed inline by CLI handlers; not exported as part of
    ``src.workspace.lib`` because it depends on ``Board``.
    """

    def __init__(self, board: Any) -> None:  # noqa: ANN401 — avoids a hard import
        self._board = board

    async def get(self, name: str) -> Any | None:  # noqa: ANN401
        existing = self._board.configs.get(name)
        if existing is None:
            return None
        return copy.deepcopy(existing)

    async def set(self, name: str, value: Any) -> None:  # noqa: ANN401
        if not isinstance(value, dict):
            self._board.configs[name] = copy.deepcopy(value)
            return
        existing = self._board.configs.get(name)
        if isinstance(existing, dict):
            merged = dict(existing)
            for k, v in value.items():
                merged[k] = copy.deepcopy(v)
            self._board.configs[name] = merged
        else:
            self._board.configs[name] = copy.deepcopy(value)

    async def list_all(self) -> dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in self._board.configs.items()}

    async def clear(self) -> None:
        self._board.configs.clear()
        self._board.active_config = None

    async def get_active(self) -> str | None:
        v = getattr(self._board, "active_config", None)
        return v if isinstance(v, str) else None

    async def set_active(self, name: str) -> None:
        self._board.active_config = name


__all__ = ["BoardBackedClaimKV"]
