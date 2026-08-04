"""Data-only ingestion for the public ats-scrapers company inventory.

This package deliberately consumes only published artifacts.  It never imports
or executes upstream scraper code; Jobseek remains the owner of every monitor.
"""

from __future__ import annotations

from src.ats_inventory.compat import COMPATIBILITY, Compatibility
from src.ats_inventory.source import InventorySource, SourceValidationError

__all__ = [
    "COMPATIBILITY",
    "Compatibility",
    "InventorySource",
    "SourceValidationError",
]
