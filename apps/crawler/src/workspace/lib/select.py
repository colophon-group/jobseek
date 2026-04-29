"""Pure async select functions: ``select_monitor`` and ``select_scraper``.

Lifted from ``src.workspace.commands.crawl.select_monitor`` /
``select_scraper``.  The functions store named monitor / scraper configs
in a :class:`~src.workspace.lib.claim_kv.ClaimKV` slot keyed by ``name``
and update the active-config pointer.

State shape inside ``claim_kv``
-------------------------------

Each named config slot stores a dict of the shape::

    {
        "monitor_type": str | None,
        "monitor_config": dict,
        "scraper_type": str | None,
        "scraper_config": dict,
    }

``select_monitor`` writes ``monitor_type`` / ``monitor_config`` and
preserves any ``scraper_*`` fields previously stored under the same
name (last-write-wins on the monitor half only).  ``select_scraper``
mirrors that contract for the scraper half.

The CLI adapter is responsible for board / registry validation and for
human-readable output; the lib never prints and never calls
``sys.exit``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

from src.workspace.lib.claim_kv import ClaimKV
from src.workspace.lib.exceptions import WsConfigInvalid


@dataclass
class SelectResult:
    """Structured result of a select call.

    Attributes:
        name: The config slot the result was written to.
        kind: ``"monitor"`` for :func:`select_monitor`, ``"scraper"`` for
            :func:`select_scraper`.
        type: The selected monitor / scraper type (mirrored from input).
        config: The config dict actually persisted (may be cleaned).
        active: Whether ``name`` is now the active config (always ``True``
            today; reserved for callers that want to assert/inspect).
    """

    name: str
    kind: Literal["monitor", "scraper"]
    type: str
    config: dict[str, Any] = field(default_factory=dict)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "type": self.type,
            "config": dict(self.config),
            "active": self.active,
        }


# ── Helpers ──────────────────────────────────────────────────────────


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise WsConfigInvalid("select: 'name' must be a non-empty string")
    if name.startswith("__") and name.endswith("__"):
        raise WsConfigInvalid(f"select: 'name' {name!r} is reserved (cannot start/end with '__')")


def _validate_type(type_value: str, kind: str) -> None:
    if not isinstance(type_value, str) or not type_value.strip():
        raise WsConfigInvalid(f"select: '{kind}_type' must be a non-empty string")


async def _read_slot(claim_kv: ClaimKV, name: str) -> dict[str, Any]:
    """Return the slot dict for ``name`` (empty dict if absent or wrong shape)."""
    existing = await claim_kv.get(name)
    if isinstance(existing, dict):
        return copy.deepcopy(existing)
    return {}


# ── Public lib functions ─────────────────────────────────────────────


async def select_monitor(
    claim_kv: ClaimKV,
    monitor_type: str,
    name: str,
    config: dict[str, Any] | None = None,
) -> SelectResult:
    """Persist a named monitor configuration to ``claim_kv``.

    Args:
        claim_kv: Per-claim KV store; the named slot under ``name`` is
            updated. Any pre-existing ``scraper_type`` / ``scraper_config``
            on the same slot is preserved (so re-selecting a monitor on
            the same config name does not erase a previously-selected
            scraper).
        monitor_type: Non-empty monitor type string.  Validation against
            the runtime registry is the CLI adapter's responsibility —
            the lib only enforces non-empty.
        name: Non-empty config slot name.  Reserved sentinel keys
            (double-underscore wrapped) are rejected.
        config: Monitor config dict.  Stored verbatim (deep-copied);
            ``None`` is treated as ``{}``.

    Returns:
        :class:`SelectResult` describing what was written and asserting
        the active pointer is now ``name``.

    Raises:
        WsConfigInvalid: when ``name`` or ``monitor_type`` fails validation.
    """
    _validate_name(name)
    _validate_type(monitor_type, "monitor")
    cfg = copy.deepcopy(config) if config else {}

    slot = await _read_slot(claim_kv, name)
    slot["monitor_type"] = monitor_type
    slot["monitor_config"] = cfg
    # Preserve any pre-existing scraper_* fields untouched; do NOT seed
    # ``None`` defaults — callers that want explicit nulls can pass them
    # in via :func:`select_scraper` later.

    await claim_kv.set(name, slot)
    await claim_kv.set_active(name)

    return SelectResult(name=name, kind="monitor", type=monitor_type, config=cfg)


async def select_scraper(
    claim_kv: ClaimKV,
    scraper_type: str,
    name: str,
    config: dict[str, Any] | None = None,
) -> SelectResult:
    """Persist a named scraper configuration to ``claim_kv``.

    Mirror of :func:`select_monitor` for the scraper half.  Preserves any
    pre-existing ``monitor_type`` / ``monitor_config`` on the same slot.
    """
    _validate_name(name)
    _validate_type(scraper_type, "scraper")
    cfg = copy.deepcopy(config) if config else {}

    slot = await _read_slot(claim_kv, name)
    slot["scraper_type"] = scraper_type
    slot["scraper_config"] = cfg
    # Preserve any pre-existing monitor_* fields untouched; do NOT seed
    # ``None`` defaults.

    await claim_kv.set(name, slot)
    await claim_kv.set_active(name)

    return SelectResult(name=name, kind="scraper", type=scraper_type, config=cfg)


__all__ = ["SelectResult", "select_monitor", "select_scraper"]
