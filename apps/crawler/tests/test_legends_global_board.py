"""Reviewed board and identity contracts for Legends Global."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def _company() -> dict[str, str]:
    with (_DATA / "companies.csv").open(newline="") as handle:
        return next(row for row in csv.DictReader(handle) if row["slug"] == "legends-global")


def test_legends_global_uses_only_current_nonempty_official_sources() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "legends-global"]

    assert rows == [
        {
            "company_slug": "legends-global",
            "board_slug": "legends-global-careers",
            "board_url": "https://asmglobal.wd1.myworkdayjobs.com/careers",
            "monitor_type": "workday",
            "monitor_config": "",
            "scraper_type": "workday",
            "scraper_config": "",
        },
        {
            "company_slug": "legends-global",
            "board_slug": "legends-global-europe",
            "board_url": "https://eujobs.legendsglobal.com",
            "monitor_type": "rss",
            "monitor_config": (
                '{"preset": "teamtailor", "feed_url": "https://eujobs.legendsglobal.com/jobs.rss"}'
            ),
            "scraper_type": "skip",
            "scraper_config": "",
        },
    ]


def test_legends_global_identity_matches_the_current_corporate_group() -> None:
    company = _company()
    extras = json.loads(company["extras"])

    assert company["name"] == "Legends Global"
    assert company["founded_year"] == "2008"
    assert extras["legalName"] == "Legends Hospitality Parent Holdings LLC"
    assert extras["parentOrganization"] == {"name": "Legends Hospitality"}
    assert extras["wikidataId"] == "Q133289327"
