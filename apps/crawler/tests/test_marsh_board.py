"""Reviewed source and identity contracts for Marsh."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def _row(filename: str, key: str, value: str) -> dict[str, str]:
    with (_DATA / filename).open(newline="") as handle:
        return next(row for row in csv.DictReader(handle) if row[key] == value)


def test_marsh_uses_one_complete_centralized_workday_board() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "marsh"]

    assert rows == [
        {
            "company_slug": "marsh",
            "board_slug": "marsh-careers",
            "board_url": "https://mmc.wd1.myworkdayjobs.com/MMC",
            "monitor_type": "workday",
            "monitor_config": "",
            "scraper_type": "workday",
            "scraper_config": "",
        }
    ]


def test_marsh_retains_current_legal_identity_and_finalized_assets() -> None:
    company = _row("companies.csv", "slug", "marsh")
    extras = json.loads(company["extras"])

    assert extras["legalName"] == "Marsh & McLennan Companies, Inc."
    assert extras["tickerSymbol"] == "MRSH"
    assert company["logo_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/marsh/"
        "logo-27e2d916ec778af31d5d7bc49920e7bb14128bd64d8625c82f78dc3bcd74a6e0.png"
    )
    assert company["icon_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/marsh/"
        "icon-ae0d7d91e104b2faa36f0856893edba4fe29418b264db3b4aae08510b1c7f84d.webp"
    )
    assert not (_DATA / "images/marsh").exists()


def test_marsh_description_preserves_the_2026_rebrand_context() -> None:
    description = _row("company_descriptions.csv", "slug", "marsh")

    assert "Formerly branded Marsh McLennan" in description["en"]
    assert "January 2026" in description["en"]
