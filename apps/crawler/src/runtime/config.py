"""Compatibility decoder for the current Redis/Postgres board snapshot.

This removes repeated storage decoding from worker call sites without claiming
to be the future cross-source BoardManifest.  CatalogPublisher (#7942) and the
contract gate (#7937) own that validated, language-neutral model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _integer(value: object, default: int, *, strict: bool = False) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        if strict:
            raise
        return default


def _boolean(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True, slots=True)
class BoardRuntimeConfig:
    """Current worker-facing board configuration after storage decoding."""

    board_url: str
    crawler_type: str
    company_id: str = ""
    domain: str = ""
    throttle_key: str = ""
    check_interval_minutes: int = 60
    scrape_interval_hours: int = 24
    monitor_needs_browser: bool = False
    scraper_needs_browser: bool = False
    egress_host: str = ""
    scrape_egress_host: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[Any, object],
        *,
        strict_intervals: bool = False,
    ) -> BoardRuntimeConfig:
        """Decode the Redis/Postgres mapping without leaking storage types.

        Most callers do not consume interval fields, so they keep fail-open
        defaults rather than gaining a new failure mode.  ``_BoardRecord`` uses
        ``strict_intervals=True`` to preserve its historical ``int(...)``
        failure for malformed ``check_interval_minutes``. It never consumed
        ``scrape_interval_hours``, so that field remains fail-open here.
        Positive-range validation belongs at the future
        CatalogPublisher/BoardManifest boundary, not this decoder.
        """

        return cls(
            board_url=str(raw.get("board_url") or ""),
            crawler_type=str(raw.get("crawler_type") or ""),
            company_id=str(raw.get("company_id") or ""),
            domain=str(raw.get("domain") or ""),
            throttle_key=str(raw.get("throttle_key") or ""),
            check_interval_minutes=_integer(
                raw.get("check_interval_minutes", "60"),
                60,
                strict=strict_intervals,
            ),
            scrape_interval_hours=_integer(
                raw.get("scrape_interval_hours", "24"),
                24,
            ),
            monitor_needs_browser=_boolean(raw.get("monitor_needs_browser")),
            scraper_needs_browser=_boolean(raw.get("scraper_needs_browser")),
            egress_host=str(raw.get("egress_host") or ""),
            scrape_egress_host=str(raw.get("scrape_egress_host") or ""),
            metadata=_json_object(raw.get("metadata")),
        )

    @property
    def scraper_config(self) -> dict[str, Any] | None:
        value = self.metadata.get("scraper_config")
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def as_board_record(self, board_id: str) -> dict[str, object]:
        """Return the stable persistence-facing board record shape."""

        return {
            "id": board_id,
            "company_id": self.company_id,
            "board_url": self.board_url,
            "crawler_type": self.crawler_type,
            "metadata": self.metadata,
            "check_interval_minutes": self.check_interval_minutes,
            "scraper_type": self.metadata.get("scraper_type"),
            "scraper_config": self.scraper_config,
            "throttle_key": self.throttle_key,
        }
