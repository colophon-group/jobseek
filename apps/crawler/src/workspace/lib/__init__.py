"""Pure async lib for probe / run (monitor + scraper).

This package is the importable boundary that lifts work formerly bound to
``ws probe ...`` / ``ws run ...`` click commands into pure async functions.

Purity contract (verified by ``scripts/grep-lib-purity.sh`` and
``tests/test_lib_purity.py``):

- No imports from ``src.workspace.commands``
- No imports from ``src.workspace.cli``
- No imports from ``src.workspace.output``

Lib functions:

- accept an explicit :class:`BoardConfigState` snapshot
- never mutate the input snapshot (frozen dataclass)
- never touch ``state.WorkspaceState`` / :class:`Board`
- never write to ``.workspace/`` or anywhere else on disk
- raise typed exceptions instead of calling ``out.die``
- return a JSON-serializable typed result dataclass
"""

from __future__ import annotations

from src.workspace.lib.board_config import BoardConfigState
from src.workspace.lib.exceptions import (
    WsBoardNotFound,
    WsConfigMissing,
    WsLibError,
    WsMonitorRunFailed,
    WsProbeFailed,
    WsScraperRunFailed,
)
from src.workspace.lib.probe import (
    ProbeEntry,
    ProbeMonitorResult,
    ProbeScraperResult,
    ScoredProbeEntry,
    probe_monitor,
    probe_scraper,
)
from src.workspace.lib.run import (
    RunMonitorResult,
    RunScraperResult,
    ScrapedJob,
    run_monitor,
    run_scraper,
)

__all__ = [
    "BoardConfigState",
    "ProbeEntry",
    "ProbeMonitorResult",
    "ProbeScraperResult",
    "RunMonitorResult",
    "RunScraperResult",
    "ScoredProbeEntry",
    "ScrapedJob",
    "WsBoardNotFound",
    "WsConfigMissing",
    "WsLibError",
    "WsMonitorRunFailed",
    "WsProbeFailed",
    "WsScraperRunFailed",
    "probe_monitor",
    "probe_scraper",
    "run_monitor",
    "run_scraper",
]
