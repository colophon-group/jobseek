"""Reviewed source and identity contracts for Enhabit."""

from __future__ import annotations

import csv
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def test_enhabit_has_one_exact_icims_source() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "enhabit"]

    assert rows == [
        {
            "company_slug": "enhabit",
            "board_slug": "enhabit-careers",
            "board_url": "https://careers-ehhi.icims.com/",
            "monitor_type": "icims",
            "monitor_config": "",
            "scraper_type": "json-ld",
            "scraper_config": "",
        }
    ]


def test_enhabit_identity_uses_the_legal_company_incorporation_year() -> None:
    with (_DATA / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "enhabit")

    assert company["name"] == "Enhabit Home Health & Hospice"
    assert company["website"] == "https://www.enhabit.com/"
    assert company["founded_year"] == "2014"
    # Enhabit was acquired and taken private in 2026, so no stale EHAB ticker metadata.
    assert company["extras"] == ""
