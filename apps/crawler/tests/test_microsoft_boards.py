"""Stable provider and metadata contracts for Microsoft."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def test_microsoft_uses_guarded_eightfold_inventory() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "microsoft"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "microsoft-careers2"
    assert row["board_url"] == "https://apply.careers.microsoft.com/careers"
    assert (row["monitor_type"], row["scraper_type"]) == (
        "eightfold",
        "eightfold",
    )
    assert json.loads(row["monitor_config"]) == {
        "url_filter": "/careers/job/",
        "pcsx_watermark": {"auto_full_crawl": False},
    }


def test_microsoft_metadata_keeps_real_ticker_and_finalized_assets() -> None:
    with (_DATA / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "microsoft")

    assert json.loads(company["extras"])["tickerSymbol"] == "MSFT"
    assert company["logo_url"].endswith("/microsoft/logo.png")
    assert company["icon_url"].endswith("/microsoft/icon.webp")
    assert not (_DATA / "images" / "microsoft").exists()
