"""Reviewed board and identity contracts for Jabil."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def test_jabil_uses_one_centralized_global_workday_source() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "jabil"]

    assert rows == [
        {
            "company_slug": "jabil",
            "board_slug": "jabil-careers",
            "board_url": "https://jabil.wd5.myworkdayjobs.com/jabil_careers",
            "monitor_type": "workday",
            "monitor_config": "",
            "scraper_type": "workday",
            "scraper_config": "",
        }
    ]


def test_jabil_identity_matches_the_public_company() -> None:
    with (_DATA / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "jabil")
    extras = json.loads(company["extras"])

    assert company["name"] == "Jabil"
    assert company["website"] == "https://www.jabil.com"
    assert company["founded_year"] == "1966"
    assert extras["legalName"] == "Jabil Inc."
    assert extras["tickerSymbol"] == "JBL"
    assert extras["wikidataId"] == "Q2171646"
    assert extras["sameAs"] == [
        "https://www.linkedin.com/company/jabil",
        "https://twitter.com/Jabil",
        "https://www.facebook.com/Jabil/",
        "https://www.youtube.com/@JabilCircuitInc",
    ]
