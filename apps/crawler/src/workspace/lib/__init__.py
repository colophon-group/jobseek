"""Pure async lib for probe / run / select / feedback.

This package is the importable boundary that lifts work formerly bound to
``ws probe ...`` / ``ws run ...`` / ``ws select ...`` / ``ws feedback``
click commands into pure async functions.

Purity contract (verified by ``scripts/grep-lib-purity.sh`` and
``tests/test_lib_purity.py``):

- No imports from ``src.workspace.commands``
- No imports from ``src.workspace.cli``
- No imports from ``src.workspace.output``

Lib functions:

- accept either a frozen :class:`BoardConfigState` (probe / run) or an
  injected :class:`ClaimKV` (select / feedback)
- never mutate the input snapshot (frozen dataclass)
- never touch ``state.WorkspaceState`` / :class:`Board`
- never write to ``.workspace/`` or anywhere else on disk
- raise typed exceptions instead of calling ``out.die``
- return a JSON-serializable typed result dataclass
"""

from __future__ import annotations

from src.workspace.lib.board_config import BoardConfigState
from src.workspace.lib.claim_kv import ACTIVE_KEY, ClaimKV, InMemoryClaimKV
from src.workspace.lib.exceptions import (
    WsBoardNotFound,
    WsConfigInvalid,
    WsConfigMissing,
    WsFeedbackIncomplete,
    WsLibError,
    WsMonitorRunFailed,
    WsProbeFailed,
    WsScraperRunFailed,
)
from src.workspace.lib.feedback import (
    FEEDBACK_FIELDS,
    IMPORTANT_FIELDS,
    QUALITY_VALUES,
    REQUIRED_FIELDS,
    VERDICT_VALUES,
    FeedbackResult,
    feedback,
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
from src.workspace.lib.select import SelectResult, select_monitor, select_scraper

__all__ = [
    "ACTIVE_KEY",
    "BoardConfigState",
    "ClaimKV",
    "FEEDBACK_FIELDS",
    "FeedbackResult",
    "IMPORTANT_FIELDS",
    "InMemoryClaimKV",
    "ProbeEntry",
    "ProbeMonitorResult",
    "ProbeScraperResult",
    "QUALITY_VALUES",
    "REQUIRED_FIELDS",
    "RunMonitorResult",
    "RunScraperResult",
    "ScoredProbeEntry",
    "ScrapedJob",
    "SelectResult",
    "VERDICT_VALUES",
    "WsBoardNotFound",
    "WsConfigInvalid",
    "WsConfigMissing",
    "WsFeedbackIncomplete",
    "WsLibError",
    "WsMonitorRunFailed",
    "WsProbeFailed",
    "WsScraperRunFailed",
    "feedback",
    "probe_monitor",
    "probe_scraper",
    "run_monitor",
    "run_scraper",
    "select_monitor",
    "select_scraper",
]
