"""Typed exceptions raised by the workspace lib.

Replaces ``out.die(...)`` calls in the click handlers — the lib never
prints and never calls ``sys.exit``.  Callers (the CLI adapter or other
agents) catch these and decide how to surface the failure.
"""

from __future__ import annotations


class WsLibError(Exception):
    """Base class for all lib errors."""


class WsBoardNotFound(WsLibError):
    """Raised when the requested workspace / board does not exist."""


class WsConfigMissing(WsLibError):
    """Raised when the board lacks the configuration required for the operation.

    Examples: ``run_monitor`` called with no monitor selected; ``run_scraper``
    called with no scraper selected; ``run_scraper`` called with no sample URLs
    available and no override list provided.
    """


class WsProbeFailed(WsLibError):
    """Raised when a probe (monitor or scraper) hits a fatal upstream error.

    The lib normally returns partial results when *individual* probes fail
    (one entry per monitor/scraper type — failed entries have ``metadata=None``).
    This exception is for fatal failures (e.g. cannot bring up Playwright).
    """


class WsMonitorRunFailed(WsLibError):
    """Raised when ``monitor_one`` raises an exception.

    Wraps the underlying exception; ``__cause__`` carries the original.
    The CLI adapter inspects ``__cause__`` to print recovery hints.
    """


class WsScraperRunFailed(WsLibError):
    """Raised when ``scrape_one`` raises a non-HTTP-status exception."""
