"""Data-only ingestion for the public ats-scrapers company inventory.

This package deliberately consumes only published artifacts.  It never imports
or executes upstream scraper code; Jobseek remains the owner of every monitor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.ats_inventory.compat import COMPATIBILITY, Compatibility

if TYPE_CHECKING:
    from src.ats_inventory.impact import ImpactCache, ImpactValidationError
    from src.ats_inventory.source import InventorySource, SourceValidationError


def __getattr__(name: str) -> Any:  # noqa: ANN401 - module export types vary
    """Load the heavier cache/source implementations only when requested."""

    if name in {"ImpactCache", "ImpactValidationError"}:
        from src.ats_inventory.impact import ImpactCache, ImpactValidationError

        value = {
            "ImpactCache": ImpactCache,
            "ImpactValidationError": ImpactValidationError,
        }[name]
    elif name in {"InventorySource", "SourceValidationError"}:
        from src.ats_inventory.source import InventorySource, SourceValidationError

        value = {
            "InventorySource": InventorySource,
            "SourceValidationError": SourceValidationError,
        }[name]
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


__all__ = [
    "COMPATIBILITY",
    "Compatibility",
    "ImpactCache",
    "ImpactValidationError",
    "InventorySource",
    "SourceValidationError",
]
