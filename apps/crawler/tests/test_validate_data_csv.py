from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from src.core.monitors import all_monitor_types
from src.core.scrapers import all_scraper_types


def _load_validator() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "validate_data_csv.py"
    spec = importlib.util.spec_from_file_location("validate_data_csv", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def test_csv_validator_types_match_runtime_registries():
    assert all_monitor_types() == validator.ALLOWED_MONITOR_TYPES
    assert {"", *all_scraper_types()} == validator.ALLOWED_SCRAPER_TYPES


def test_occupation_header_accepts_extra_locale_columns():
    validator.validate_header(
        "occupations.csv",
        ["slug", "parent", "domain", "en", "de", "fr", "it", "pl", "es", "aliases"],
    )


def test_occupation_header_rejects_non_locale_extra_columns():
    with pytest.raises(validator.ValidationError, match="unexpected non-locale column"):
        validator.validate_header(
            "occupations.csv",
            ["slug", "parent", "domain", "en", "de", "fr", "it", "notes", "aliases"],
        )
