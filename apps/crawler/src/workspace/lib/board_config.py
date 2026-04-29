"""Immutable per-board state snapshot consumed by the lib functions.

The CLI handlers in ``src.workspace.commands.crawl`` build a
:class:`BoardConfigState` from a loaded ``Board`` and pass it to lib
functions.  The lib functions never read or mutate :class:`Board` /
:class:`Workspace` directly — this dataclass is the only state contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BoardConfigState:
    """Frozen snapshot of the per-board state needed by probe / run lib.

    ``frozen=True`` — lib functions cannot mutate it. Use ``dataclasses.replace``
    if you need a derived snapshot.

    Args:
        board_url: The board URL to probe / monitor.
        alias: Board alias (used for artifact paths by the CLI side, never by lib).
        slug: Workspace slug (used for artifact paths by the CLI side, never by lib).
        monitor_type: Monitor type name, e.g. ``"sitemap"`` / ``"greenhouse"``.
            ``None`` means no monitor selected.
        monitor_config: Monitor config dict (frozen by convention; lib treats as read-only).
        scraper_type: Scraper type name. ``None`` means no scraper selected.
        scraper_config: Scraper config dict.
        sample_urls: Sample URLs from a previous monitor run (used by run_scraper).
        ssl_verify: Whether to verify TLS certificates for outbound HTTP.
        use_proxy: Whether to route monitor/scraper traffic through the proxy.
    """

    board_url: str
    alias: str = ""
    slug: str = ""
    monitor_type: str | None = None
    monitor_config: dict[str, Any] = field(default_factory=dict)
    scraper_type: str | None = None
    scraper_config: dict[str, Any] = field(default_factory=dict)
    sample_urls: tuple[str, ...] = ()
    ssl_verify: bool = True
    use_proxy: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (frozen-aware)."""
        return {
            "board_url": self.board_url,
            "alias": self.alias,
            "slug": self.slug,
            "monitor_type": self.monitor_type,
            "monitor_config": dict(self.monitor_config),
            "scraper_type": self.scraper_type,
            "scraper_config": dict(self.scraper_config),
            "sample_urls": list(self.sample_urls),
            "ssl_verify": self.ssl_verify,
            "use_proxy": self.use_proxy,
        }
