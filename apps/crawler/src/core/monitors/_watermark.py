"""Generic high-water-mark state for incremental monitors.

Any paginating monitor that returns results sorted newest-first can use this
module to persist a watermark in ``job_board.metadata.<key>_watermark`` and
decide whether a given run should do a full crawl or an incremental top-up.

The state is serialized as a single shallow JSONB object under
``metadata[state.key]`` so that the existing ``_UPDATE_METADATA`` query
(which uses PostgreSQL's shallow ``||`` merge) replaces the whole watermark
subkey atomically. Callers build a ``MonitorResult`` with
``metadata_updates=to_metadata_patch(state)`` and the pipeline writes it.

Used by: eightfold (first caller). Designed so amazon, accenture, oracle_hcm
and any other paginating monitor can adopt the same pattern with minimal code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class WatermarkState:
    """State for an incremental monitor, persisted under
    ``job_board.metadata.<key>_watermark``."""

    #: The JSONB subkey name, e.g. ``"pcsx_watermark"``.
    key: str
    #: Highest comparison value seen (e.g. ``postedTs`` unix seconds).
    max_ts: int = 0
    #: Wall-clock of the last successful full crawl (None = never).
    last_full_at: datetime | None = None
    #: Wall-clock of the last successful incremental crawl (None = never).
    last_incremental_at: datetime | None = None
    #: How many days between forced full re-crawls.
    interval_days: int = 7
    #: Cached upstream-probe result. ``None`` means "not yet probed".
    enabled: bool | None = None
    #: If False, scheduled runs with a missing watermark fall back to
    #: sitemap-only instead of attempting a full crawl. Operators set this
    #: to False on very large boards and run a manual backfill.
    auto_full_crawl: bool = True
    #: Opaque monitor-specific fields (host, domain, last error, etc.).
    extra: dict = field(default_factory=dict)

    def needs_full_crawl(self, now: datetime | None = None) -> bool:
        """True when this is the first run or the full-crawl interval elapsed."""
        now = now or datetime.now(UTC)
        if self.max_ts == 0 or self.last_full_at is None:
            return True
        return (now - self.last_full_at) >= timedelta(days=self.interval_days)


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO 8601 datetime string if present, else return None."""
    if not value or not isinstance(value, str):
        return None
    try:
        # ``fromisoformat`` accepts 'Z' suffix as of Python 3.11.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read(board_metadata: dict | None, key: str) -> WatermarkState:
    """Parse the watermark state from ``job_board.metadata[key]``.

    Returns a ``WatermarkState`` with default values when the subkey is
    missing or malformed — callers don't need to distinguish "never run"
    from "corrupt state"; both result in a first-run / full-crawl branch.
    """
    state = WatermarkState(key=key)
    if not board_metadata:
        return state
    raw = board_metadata.get(key)
    if not isinstance(raw, dict):
        return state
    state.max_ts = int(raw.get("max_ts") or 0)
    state.last_full_at = _parse_iso(raw.get("last_full_at"))
    state.last_incremental_at = _parse_iso(raw.get("last_incremental_at"))
    interval = raw.get("interval_days")
    if isinstance(interval, int) and interval > 0:
        state.interval_days = interval
    enabled = raw.get("enabled")
    if isinstance(enabled, bool):
        state.enabled = enabled
    auto = raw.get("auto_full_crawl")
    if isinstance(auto, bool):
        state.auto_full_crawl = auto
    extra = raw.get("extra")
    if isinstance(extra, dict):
        state.extra = extra
    return state


def to_metadata_patch(state: WatermarkState) -> dict:
    """Serialize to a shallow JSONB-merge payload.

    Returns ``{state.key: {...full snapshot...}}`` so the existing
    ``_UPDATE_METADATA`` query (which uses shallow ``||``) replaces the
    whole subkey atomically. Passing a partial subkey would leave stale
    keys in place because JSONB ``||`` does not recurse.
    """
    inner: dict = {
        "max_ts": state.max_ts,
        "interval_days": state.interval_days,
        "auto_full_crawl": state.auto_full_crawl,
    }
    if state.last_full_at is not None:
        inner["last_full_at"] = state.last_full_at.isoformat()
    if state.last_incremental_at is not None:
        inner["last_incremental_at"] = state.last_incremental_at.isoformat()
    if state.enabled is not None:
        inner["enabled"] = state.enabled
    if state.extra:
        inner["extra"] = state.extra
    return {state.key: inner}
