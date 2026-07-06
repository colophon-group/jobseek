"""Per-claim KV store interface — Python mirror of the TS ``claim-kv``.

This module defines the :class:`ClaimKV` :class:`~typing.Protocol` that
``select_monitor`` / ``select_scraper`` / ``feedback`` operate against,
plus an in-memory implementation used by the workspace lib tests and by
CLI integration tests.

State that previously lived in ``boards/<alias>.yaml`` (named configs +
the active config pointer) lives under named keys in ``ClaimKV``.

Active-config tracking
----------------------

The "active named config" referenced by :func:`feedback` is a separate
slot identified by the reserved key :data:`ACTIVE_KEY`.  ``set_active``
stores the active name there; ``get_active`` reads it back.

Concrete implementations
------------------------

- :class:`InMemoryClaimKV` — used by tests and by CLI adapters that just
  need an ephemeral store.
- The HTTP route in J5 will pass an implementation backed by the TS
  Postgres module.
- The CLI adapters in ``commands/crawl.py`` use a board-backed adapter
  defined alongside them so the on-disk YAML format stays compatible.

Purity
------

This module imports only from :mod:`typing`, :mod:`asyncio`-friendly
stdlib, and the local :mod:`exceptions` module — no CLI dependencies, no
disk I/O, no networking.

@see colophon-group/jobseek#2756
@see colophon-group/jobseek#2757 (TS counterpart in ``apps/murmur-shim/src/lib/murmur/claim-kv.ts``)
"""

from __future__ import annotations

import copy
from typing import Any, Protocol, runtime_checkable

# Reserved key under which the active-config name is tracked.
#
# The leading/trailing double-underscore makes accidental collision with
# user-supplied config names extremely unlikely (config names are slugs
# from CLI flags / agent input; double-dunder is not a legal slug).
ACTIVE_KEY = "__active__"


@runtime_checkable
class ClaimKV(Protocol):
    """Per-claim key-value store contract.

    All methods are async because the production implementation talks to
    Postgres over HTTP.  The in-memory implementation runs synchronously
    under an ``asyncio`` driver but exposes the same surface so callers
    can be written once.

    Implementations MUST:

    - Round-trip JSON-serializable values without coercion.
    - Treat :data:`ACTIVE_KEY` as a normal slot for storage purposes —
      :meth:`list_all` MAY filter it out (callers iterate "user named
      configs" via :meth:`list_all`); :meth:`get` / :meth:`set` MUST not
      filter it (the active-name helpers depend on direct slot access).
    - Be safe under last-write-wins concurrency (the J3 TS impl is, via
      ``ON CONFLICT``).
    - Never raise on a missing ``name``; :meth:`get` returns ``None``.
    """

    async def get(self, name: str) -> Any | None:  # noqa: ANN401
        """Return the value stored under ``name``, or ``None`` if missing."""
        raise NotImplementedError

    async def set(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Store ``value`` under ``name`` (overwrite if present)."""
        raise NotImplementedError

    async def list_all(self) -> dict[str, Any]:
        """Return a snapshot mapping name → value.

        Implementations SHOULD exclude :data:`ACTIVE_KEY` from the result
        so callers iterating "named configs" don't see the sentinel.
        """
        raise NotImplementedError

    async def clear(self) -> None:
        """Remove every slot belonging to this claim."""
        raise NotImplementedError

    async def get_active(self) -> str | None:
        """Return the active config name set by :meth:`set_active`."""
        raise NotImplementedError

    async def set_active(self, name: str) -> None:
        """Mark ``name`` as the active config slot."""
        raise NotImplementedError


class InMemoryClaimKV:
    """Reference :class:`ClaimKV` implementation backed by a ``dict``.

    Used by the workspace lib tests and by callers that want an
    ephemeral store (e.g. unit tests for the CLI adapter).  Values are
    deep-copied on store and load to mimic the JSON round-trip the
    production Postgres-backed impl performs — that guarantees lib
    behavior does not silently rely on shared references.
    """

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = {}
        if initial:
            for k, v in initial.items():
                self._data[k] = copy.deepcopy(v)

    async def get(self, name: str) -> Any | None:  # noqa: ANN401
        if name not in self._data:
            return None
        return copy.deepcopy(self._data[name])

    async def set(self, name: str, value: Any) -> None:  # noqa: ANN401
        self._data[name] = copy.deepcopy(value)

    async def list_all(self) -> dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in self._data.items() if k != ACTIVE_KEY}

    async def clear(self) -> None:
        self._data.clear()

    async def get_active(self) -> str | None:
        v = self._data.get(ACTIVE_KEY)
        return v if isinstance(v, str) else None

    async def set_active(self, name: str) -> None:
        self._data[ACTIVE_KEY] = name


__all__ = ["ACTIVE_KEY", "ClaimKV", "InMemoryClaimKV"]
